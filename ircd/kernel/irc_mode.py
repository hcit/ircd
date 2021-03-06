from command import command, is_op
from ..common.util import split


def mode_chan(server, user, target, args):
    chan = server.find_chan(target)
    if not chan:
        server.send_reply(user, 'ERR_NOSUCHCHANNEL', target)
        return

    # parameterless mode: show current modes
    if not args:
        server.send_reply(user, 'RPL_CHANNELMODEIS',
                          chan['name'], chan['modes'])
        return

    # special case for banlist
    if args == 'b' or args == '+b':
        # users should use ACCESS instead
        server.send_reply(user, 'RPL_ENDOFBANLIST', chan['name'])
        return

    # all modes require op status
    user_data = server.chan_nick(chan, user['nick'])
    if not is_op(user_data):
        server.send_reply(user, 'ERR_CHANOPRIVSNEEDED', chan['name'])
        return

    chars, rest = split(args, 1)
    adding = True

    for c in chars:
        if c in '+-':
            adding = c == '+'

        # op/voice
        elif c in 'qov':
            target, rest = split(rest, 1)

            if not target:
                # no target supplied
                continue

            # only owners are allowed to +q/-q
            if c == 'q' and 'q' not in user_data['modes']:
                continue

            target_data = server.chan_nick(chan, target)
            if target_data is None:
                server.send_reply(
                    user, 'ERR_USERNOTINCHANNEL', target, chan['name'])
                continue

            target_modes = target_data['modes']

            # check if it's necessary to add or remove the mode
            if not ((c in target_modes) ^ adding):
                continue

            if adding:
                target_modes += c
            else:
                target_modes = target_modes.replace(c, '')

            target_data['modes'] = target_modes
            server.set_chan_nick(chan, target, target_data)

            args = '%s%s %s' % ('+' if adding else '-', c, target)
            server.send_chan(user, 'MODE', chan, args)

        elif c in 'm':
            chan_modes = chan['modes']
            if not ((c in chan_modes) ^ adding):
                continue

            if adding:
                chan_modes += c
            else:
                chan_modes = chan_modes.replace(c, '')

            chan['modes'] = chan_modes
            server.save_chan(chan)

            args = '%s%s' % ('+' if adding else '-', c)
            server.send_chan(user, 'MODE', chan, args)

        else:
            server.send_reply(user, 'ERR_UNKNOWNMODE', c)


@command(auth=True, args=1)
def cmd_mode(server, user, target, args):
    if target[0] == '#':
        mode_chan(server, user, target, args)
    else:
        # user mode
        pass
