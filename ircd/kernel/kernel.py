import collections
import msgpack as json
import logging
import timeout
import command
import redis
import replies
from ..common.util import split


def _prefix(tag):
    return tag.split(':', 1)[0]


class Kernel(object):
    def __init__(self, config):
        self.config = config
        self.running = True
        self.message = None
        self.name = config.server_name

        command.load_commands()
        self.redis = redis.StrictRedis(db=config.redis_db)

        self.init_timeout(config)

    def init_timeout(self, config):
        """
        Initialize the timeout tracker
        """
        self.timeout = timeout.Timeout(self, config)

        # track all connected users
        keys = self.redis.keys('user:*')
        for key in keys:
            tag = key.split(':', 1)[1]
            self.timeout.update(tag)

    def loop(self):
        """
        Infinite get message/process message loop

        Messages are read from a redis list that works like a message queue.
        """
        logging.info('IRCd started')

        while self.running:
            self.timeout.check()
            ret = self.redis.blpop('mq:kernel', 1)
            if ret:
                _, self.message = ret
                self.process_message(self.message)
                self.message = None

    def stop(self):
        """
        Stop the infinite loop

        This method can be called from a signal handler or from inside
        process_message().
        """

        logging.info('IRCd stopped')

        # exit the loop during the next iteration
        self.running = False

        if not self.message:
            # if a message is not being processed the process might be
            # blocked in redis.blpop(), in this case just exit
            import sys
            sys.exit(0)

    def process_message(self, message):
        kind, origin, data = message.split(' ', 2)

        if kind == 'message':
            self.user_message(origin, data)

        elif kind == 'connect':
            self.user_connect(origin, data)

        elif kind == 'disconnect':
            self.user_disconnect(origin, data)

        elif kind == 'reset':
            self.server_reset(origin, data)

        elif kind == 'shutdown':
            self.stop()

    def user_message(self, tag, message):
        logging.debug('message %s %s', tag, message)

        self.timeout.update(tag)

        user = self.load_user(tag)
        if not user:
            logging.error('user %s not found' % tag)
            return

        try:
            message.decode('utf-8')
        except:
            self.send_reply(user, 'ERR_NONUTF8')
            return

        command.dispatch(self, user, message)

    def user_connect(self, tag, address):
        logging.debug('connect %s %s', tag, address)

        self.timeout.update(tag)

        user = {
            'ip': address,
            'tag': tag,
            'nick': '*'
        }
        self.save_user(user)

        prefix = _prefix(tag)
        self.redis.sadd('server-users:' + prefix, tag)

    def user_disconnect(self, tag, reason):
        logging.debug('disconnect %s %s', tag, reason)

        self.timeout.remove(tag)

        user = self.load_user(tag)
        if not user:
            logging.error('user %s not found' % tag)
            return

        chans = self.user_chans(user)
        for chan_name in chans:
            command.dispatch(self, user, 'PART %s' % chan_name)

        if 'auth' in user:
            self.unregister_nick(user)

        self.redis.delete('user:' + tag)

        prefix = _prefix(tag)
        self.redis.srem('server-users:' + prefix, tag)

    def server_reset(self, prefix, reason):
        logging.debug('reset %s %s', prefix, reason)

        tags = self.redis.smembers('server-users:' + prefix)
        for tag in tags:
            self.user_disconnect(tag, reason)

    def send_chan(self, user, command, chan, args='', others_only=False):
        tags = self.redis.smembers('chan-users:' + chan['name'])
        if others_only:
            tags.discard(user['tag'])
        self.send_command(tags, user, command, chan['name'], args)

    def send_command(self, tags, source, command, target, args):
        self.send(tags, ':%s %s %s %s' % (source['id'], command, target, args))

    def send_reply(self, user, reply, *args):
        numeric, format = replies.replies.get(reply, (reply, '%s'))
        self.send(user['tag'], ':%s %s %s %s' % (
            self.name, numeric, user['nick'], format % args))

    def send_visible(self, user, reply, *args):
        numeric, format = replies.replies.get(reply, (reply, '%s'))
        message = ':%s %s %s' % (user['id'], numeric, format % args)
        tags = set()
        for chan_name in self.user_chans(user):
            chan_tags = self.redis.smembers('chan-users:' + chan_name)
            tags.update(chan_tags)
        tags.discard(user['tag'])
        self.send(tags, message)

    def send(self, tags, message):
        message = message.strip()
        logging.debug('send %s' % message)

        if type(tags) in [str, unicode]:
            prefix = _prefix(tags)
            self.redis.rpush('mq:' + prefix, '%s %s\r\n' % (tags, message))
            return

        prefixes = collections.defaultdict(list)
        for tag in tags:
            prefixes[_prefix(tag)].append(tag)

        for prefix, tags in prefixes.iteritems():
            tags = ','.join(tags)
            self.redis.rpush('mq:' + prefix, '%s %s\r\n' % (tags, message))

    def disconnect(self, user):
        tag = user['tag']
        self.redis.rpush('mq:' + _prefix(tag), '%s ' % tag)

    def load_user(self, tag):
        serialized = self.redis.get('user:' + tag)
        return serialized and json.loads(serialized)

    def save_user(self, user):
        serialized = json.dumps(user)
        self.redis.set('user:' + user['tag'], serialized)

    def find_or_create_chan(self, chan_name):
        chan = self.find_chan(chan_name)
        if chan:
            return chan, False

        chan = {
            'name': chan_name,
            'topic': '',
            'modes': ''
        }
        self.save_chan(chan)
        return chan, True

    def find_chan(self, chan_name):
        chan = self.redis.get('chan:' + chan_name)
        return chan and json.loads(chan)

    def save_chan(self, chan):
        serialized = json.dumps(chan)
        chan = self.redis.set('chan:' + chan['name'], serialized)

    def join_chan(self, user, chan, data):
        self.set_chan_nick(chan, user['nick'], data)
        self.redis.sadd('chan-users:' + chan['name'], user['tag'])
        self.redis.sadd('user-chans:' + user['tag'], chan['name'])

    def part_chan(self, user, chan):
        self.redis.hdel('chan-nicks:' + chan['name'], user['nick'])
        self.redis.srem('chan-users:' + chan['name'], user['tag'])
        self.redis.srem('user-chans:' + user['tag'], chan['name'])

        if not self.chan_count(chan):
            self.destroy_chan(chan)

    def nick_in_chan(self, user, chan):
        return self.redis.hexists('chan-nicks:' + chan['name'], user['nick'])

    def user_in_chan(self, user, chan):
        return self.redis.sismember('chan-users:' + chan['name'], user['tag'])

    def user_chans(self, user):
        return self.redis.smembers('user-chans:' + user['tag'])

    def chan_count(self, chan):
        return self.redis.hlen('chan-nicks:' + chan['name'])

    def chan_nicks(self, chan):
        nicks = self.redis.hgetall('chan-nicks:' + chan['name'])
        return [(nick, json.loads(data)) for nick, data in nicks.iteritems()]

    def chan_nick(self, chan, nick):
        serialized = self.redis.hget('chan-nicks:' + chan['name'], nick)
        return serialized and json.loads(serialized)

    def set_chan_nick(self, chan, nick, data):
        serialized = json.dumps(data)
        self.redis.hset('chan-nicks:' + chan['name'], nick, serialized)

    def destroy_chan(self, chan):
        self.redis.delete('chan-access:' + chan['name'])
        self.redis.delete('chan:' + chan['name'])

    def register_nick(self, user):
        self.redis.sadd('nick-users:' + user['nick'], user['tag'])

    def unregister_nick(self, user):
        self.redis.srem('nick-users:' + user['nick'], user['tag'])

    def find_nick(self, nick):
        return self.redis.smembers('nick-users:' + nick)

    def access_list_all(self, chan):
        acl_dict = self.redis.hgetall('chan-access:' + chan['name'])
        for key, value in acl_dict.iteritems():
            level, mask = key.split()
            timeout, user, reason = split(value, 2)
            timeout = int(timeout)
            yield (level, mask, timeout, user, reason)

    def access_list_add(self, chan, level, mask, timeout, user, reason):
        key = '%s %s' % (level, mask)
        value = '%d %s %s' % (timeout, user['id'], reason)
        self.redis.hset('chan-access:' + chan['name'], key, value)

    def access_list_del(self, chan, level, mask):
        key = '%s %s' % (level, mask)
        self.redis.hdel('chan-access:' + chan['name'], key)
