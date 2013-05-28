from testutil import *


def test_join1(k0):
    raw('connect test:__1 ::1')

    msg('JOIN #a')
    assert code() == '451'  # not registered

    msg('nick test')
    msg('user test')
    while pop():
        pass

    msg('JOIN #!')
    assert code() == '479'  # invalid channel name


def test_join2(k1):
    msg('JOIN #a')
    assert pop().split()[2:] == ['JOIN', '#a']
    assert code() == '331'
    assert code() == '353'
    assert code() == '366'

    msg('JOIN #a')
    assert code() == '901'  # already joined


def test_join3(k1):
    msg('JOIN #a')
    while pop():
        pass

    raw('connect test:__2 ::2')
    msg('NICK test1', 2)
    msg('USER test1', 2)
    assert code() == '001'
    while pop():
        pass

    msg('JOIN #a', 2)
    assert code() == '901'  # already joined


def test_part1(k1):
    msg('PART #a')
    assert code() == '403'  # no such channel

    user(2)

    msg('JOIN #a', 2)
    while pop():
        pass

    msg('PART #a')
    assert code() == '442'  # not on chan

    msg('PART #a', 2)
    assert pop() == 'test:__2 :test2!test2@::2 PART #a\r\n'


def test_part2(k1):
    msg('JOIN #a')
    while pop():
        pass

    msg('JOIN #b')
    while pop():
        pass

    user(2)
    msg('JOIN #a', 2)
    while pop():
        pass

    raw('disconnect test:__1 reason')
    assert pop() == 'test:__1 :test1!test1@::1 PART #b\r\n'


def test_names(k1):
    msg('JOIN #a')
    raw('connect test:__2 ::2')
    msg('NICK test2', 2)
    msg('USER test2', 2)
    msg('JOIN #a', 2)

    while pop():
        pass

    msg('NAMES #a')
    _, serv, code_, equal, nick, chan, nicks = pop().split(' ', 6)
    assert code_ == '353'
    nicks = nicks[1:].split()
    assert '@test1' in nicks
    assert 'test2' in nicks
    assert code() == '366'


def test_multiple(k1):
    msg('JOIN #a,#b')
    while pop():
        pass

    msg('PRIVMSG #a :oi')
    msg('PRIVMSG #b :oi')
    assert pop() is None
