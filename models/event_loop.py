#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import libvirt
import select
import errno
import time
import threading


__author__ = 'James Iter'
__date__ = '2017/6/14'
__contact__ = 'james.iter.cn@gmail.com'
__copyright__ = '(c) 2017 by James Iter.'


event_impl = "poll"

#
# This general purpose event loop will support waiting for file handle
# I/O and errors events, as well as scheduling repeatable timers with
# a fixed interval.
#
# It is a pure python implementation based around the poll() API
#


class VirEventLoopPoll(object):
    # This class contains the data we need to track for a
    # single file handle
    class VirEventLoopPollHandle(object):
        def __init__(self, handle, fd, events, cb, opaque):
            self.handle = handle
            self.fd = fd
            self.events = events
            self.cb = cb
            self.opaque = opaque

        def get_id(self):
            return self.handle

        def get_fd(self):
            return self.fd

        def get_events(self):
            return self.events

        def set_events(self, events):
            self.events = events

        def dispatch(self, events):
            self.cb(self.handle,
                    self.fd,
                    events,
                    self.opaque)

    # This class contains the data we need to track for a
    # single periodic timer
    class VirEventLoopPollTimer:
        def __init__(self, timer, interval, cb, opaque):
            self.timer = timer
            self.interval = interval
            self.cb = cb
            self.opaque = opaque
            self.lastfired = 0

        def get_id(self):
            return self.timer

        def get_interval(self):
            return self.interval

        def set_interval(self, interval):
            self.interval = interval

        def get_last_fired(self):
            return self.lastfired

        def set_last_fired(self, now):
            self.lastfired = now

        def dispatch(self):
            self.cb(self.timer,
                    self.opaque)

    def __init__(self):
        self.poll = select.poll()
        self.pipetrick = os.pipe()
        self.pendingWakeup = False
        self.runningPoll = False
        self.nextHandleID = 1
        self.nextTimerID = 1
        self.handles = []
        self.timers = []
        self.cleanup = []
        self.quit = False

        # The event loop can be used from multiple threads at once.
        # Specifically while the main thread is sleeping in poll()
        # waiting for events to occur, another thread may come along
        # and add/update/remove a file handle, or timer. When this
        # happens we need to interrupt the poll() sleep in the other
        # thread, so that it'll see the file handle / timer changes.
        #
        # Using OS level signals for this is very unreliable and
        # hard to implement correctly. Thus we use the real classic
        # "self pipe" trick. A anonymous pipe, with one end registered
        # with the event loop for input events. When we need to force
        # the main thread out of a poll() sleep, we simple write a
        # single byte of data to the other end of the pipe.
        self.poll.register(self.pipetrick[0], select.POLLIN)

    # Calculate when the next timeout is due to occur, returning
    # the absolute timestamp for the next timeout, or 0 if there is
    # no timeout due
    def next_timeout(self):
        _next = 0
        for t in self.timers:
            last = t.get_last_fired()
            interval = t.get_interval()
            if interval < 0:
                continue
            if _next == 0 or (last + interval) < _next:
                _next = last + interval

        return _next

    # Lookup a virEventLoopPollHandle object based on file descriptor
    def get_handle_by_fd(self, fd):
        for h in self.handles:
            if h.get_fd() == fd:
                return h
        return None

    # Lookup a virEventLoopPollHandle object based on its event loop ID
    def get_handle_by_id(self, handle_id):
        for h in self.handles:
            if h.get_id() == handle_id:
                return h
        return None

    # This is the heart of the event loop, performing one single
    # iteration. It asks when the next timeout is due, and then
    # calculates the maximum amount of time it is able to sleep
    # for in poll() pending file handle events.
    #
    # It then goes into the poll() sleep.
    #
    # When poll() returns, there will zero or more file handle
    # events which need to be dispatched to registered callbacks
    # It may also be time to fire some periodic timers.
    #
    # Due to the coarse granularity of scheduler timeslices, if
    # we ask for a sleep of 500ms in order to satisfy a timer, we
    # may return up to 1 scheduler timeslice early. So even though
    # our sleep timeout was reached, the registered timer may not
    # technically be at its expiry point. This leads to us going
    # back around the loop with a crazy 5ms sleep. So when checking
    # if timeouts are due, we allow a margin of 20ms, to avoid
    # these pointless repeated tiny sleeps.
    def run_once(self):
        sleep = -1
        self.runningPoll = True

        for opaque in self.cleanup:
            libvirt.virEventInvokeFreeCallback(opaque)
        self.cleanup = []

        try:
            _next = self.next_timeout()
            if _next > 0:
                now = int(time.time() * 1000)
                if now >= _next:
                    sleep = 0
                else:
                    sleep = (_next - now) / 1000.0

            events = self.poll.poll(sleep)

            # Dispatch any file handle events that occurred
            for (fd, revents) in events:
                # See if the events was from the self-pipe
                # telling us to wakup. if so, then discard
                # the data just continue
                if fd == self.pipetrick[0]:
                    self.pendingWakeup = False
                    data = os.read(fd, 1)
                    continue

                h = self.get_handle_by_fd(fd)
                if h:
                    h.dispatch(self.events_from_poll(revents))

            now = int(time.time() * 1000)
            for t in self.timers:
                interval = t.get_interval()
                if interval < 0:
                    continue

                want = t.get_last_fired() + interval
                # Deduct 20ms, since scheduler timeslice
                # means we could be ever so slightly early
                if now >= (want-20):
                    t.set_last_fired(now)
                    t.dispatch()

        except (os.error, select.error) as e:
            if e.args[0] != errno.EINTR:
                raise
        finally:
            self.runningPoll = False

    # Actually run the event loop forever
    def run_loop(self):
        self.quit = False
        while not self.quit:
            self.run_once()

    def interrupt(self):
        if self.runningPoll and not self.pendingWakeup:
            self.pendingWakeup = True
            os.write(self.pipetrick[1], 'c'.encode("UTF-8"))

    # Registers a new file handle 'fd', monitoring  for 'events' (libvirt
    # event constants), firing the callback  cb() when an event occurs.
    # Returns a unique integer identier for this handle, that should be
    # used to later update/remove it
    def add_handle(self, fd, events, cb, opaque):
        handle_id = self.nextHandleID + 1
        self.nextHandleID = self.nextHandleID + 1

        h = self.VirEventLoopPollHandle(handle_id, fd, events, cb, opaque)
        self.handles.append(h)

        self.poll.register(fd, self.events_to_poll(events))
        self.interrupt()

        return handle_id

    # Registers a new timer with periodic expiry at 'interval' ms,
    # firing cb() each time the timer expires. If 'interval' is -1,
    # then the timer is registered, but not enabled
    # Returns a unique integer identier for this handle, that should be
    # used to later update/remove it
    def add_timer(self, interval, cb, opaque):
        timer_id = self.nextTimerID + 1
        self.nextTimerID = self.nextTimerID + 1

        h = self.VirEventLoopPollTimer(timer_id, interval, cb, opaque)
        self.timers.append(h)
        self.interrupt()

        return timer_id

    # Change the set of events to be monitored on the file handle
    def update_handle(self, handle_id, events):
        h = self.get_handle_by_id(handle_id)
        if h:
            h.set_events(events)
            self.poll.unregister(h.get_fd())
            self.poll.register(h.get_fd(), self.events_to_poll(events))
            self.interrupt()

    # Change the periodic frequency of the timer
    def update_timer(self, timer_id, interval):
        for h in self.timers:
            if h.get_id() == timer_id:
                h.set_interval(interval)
                self.interrupt()

                break

    # Stop monitoring for events on the file handle
    def remove_handle(self, handle_id):
        handles = []
        for h in self.handles:
            if h.get_id() == handle_id:
                self.poll.unregister(h.get_fd())
                self.cleanup.append(h.opaque)
            else:
                handles.append(h)
        self.handles = handles
        self.interrupt()

    # Stop firing the periodic timer
    def remove_timer(self, timer_id):
        timers = []
        for h in self.timers:
            if h.get_id() != timer_id:
                timers.append(h)
            else:
                self.cleanup.append(h.opaque)
        self.timers = timers
        self.interrupt()

    # Convert from libvirt event constants, to poll() events constants
    @staticmethod
    def events_to_poll(events):
        ret = 0
        if events & libvirt.VIR_EVENT_HANDLE_READABLE:
            ret |= select.POLLIN
        if events & libvirt.VIR_EVENT_HANDLE_WRITABLE:
            ret |= select.POLLOUT
        if events & libvirt.VIR_EVENT_HANDLE_ERROR:
            ret |= select.POLLERR
        if events & libvirt.VIR_EVENT_HANDLE_HANGUP:
            ret |= select.POLLHUP
        return ret

    # Convert from poll() event constants, to libvirt events constants
    @staticmethod
    def events_from_poll(events):
        ret = 0
        if events & select.POLLIN:
            ret |= libvirt.VIR_EVENT_HANDLE_READABLE
        if events & select.POLLOUT:
            ret |= libvirt.VIR_EVENT_HANDLE_WRITABLE
        if events & select.POLLNVAL:
            ret |= libvirt.VIR_EVENT_HANDLE_ERROR
        if events & select.POLLERR:
            ret |= libvirt.VIR_EVENT_HANDLE_ERROR
        if events & select.POLLHUP:
            ret |= libvirt.VIR_EVENT_HANDLE_HANGUP
        return ret


###########################################################################
# Now glue an instance of the general event loop into libvirt's event loop
###########################################################################

# This single global instance of the event loop wil be used for
# monitoring libvirt events
eventLoop = VirEventLoopPoll()

# This keeps track of what thread is running the event loop,
# (if it is run in a background thread)
eventLoopThread = None


# These next set of 6 methods are the glue between the official
# libvirt events API, and our particular impl of the event loop
#
# There is no reason why the 'virEventLoopPoll' has to be used.
# An application could easily may these 6 glue methods hook into
# another event loop such as GLib's, or something like the python
# Twisted event framework.

def vir_event_add_handle_impl(fd, events, cb, opaque):
    global eventLoop
    return eventLoop.add_handle(fd, events, cb, opaque)


def vir_event_update_handle_impl(handle_id, events):
    global eventLoop
    return eventLoop.update_handle(handle_id, events)


def vir_event_remove_handle_impl(handle_id):
    global eventLoop
    return eventLoop.remove_handle(handle_id)


def vir_event_add_timer_impl(interval, cb, opaque):
    global eventLoop
    return eventLoop.add_timer(interval, cb, opaque)


def vir_event_update_timer_impl(timer_id, interval):
    global eventLoop
    return eventLoop.update_timer(timer_id, interval)


def vir_event_remove_timer_impl(timer_id):
    global eventLoop
    return eventLoop.remove_timer(timer_id)


# This tells libvirt what event loop implementation it
# should use
def vir_event_loop_poll_register():
    libvirt.virEventRegisterImpl(vir_event_add_handle_impl,
                                 vir_event_update_handle_impl,
                                 vir_event_remove_handle_impl,
                                 vir_event_add_timer_impl,
                                 vir_event_update_timer_impl,
                                 vir_event_remove_timer_impl)


# Directly run the event loop in the current thread
def vir_event_loop_poll_run():
    global eventLoop
    eventLoop.run_loop()


def vir_event_loop_native_run():
    while True:
        libvirt.virEventRunDefaultImpl()


# Spawn a background thread to run the event loop
def vir_event_loop_poll_start():
    global eventLoopThread
    vir_event_loop_poll_register()
    eventLoopThread = threading.Thread(target=vir_event_loop_poll_run, name="libvirtEventLoop")
    eventLoopThread.setDaemon(True)
    eventLoopThread.start()


def vir_event_loop_native_start():
    global eventLoopThread
    libvirt.virEventRegisterDefaultImpl()
    eventLoopThread = threading.Thread(target=vir_event_loop_native_run, name="libvirtEventLoop")
    eventLoopThread.setDaemon(True)
    eventLoopThread.start()


def dom_event_to_string(event):
    # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventType
    dom_event_strings = ("Defined",
                         "Undefined",
                         "Started",
                         "Suspended",
                         "Resumed",
                         "Stopped",
                         "Shutdown",
                         "PMSuspended",
                         "Crashed")
    return dom_event_strings[event]


def dom_detail_to_string(event, detail):
    # 参考地址：https://github.com/libvirt/libvirt/blob/v3.4.0-rc2/include/libvirt/libvirt-domain.h
    dom_event_strings = (
        ("Added", "Updated", "Renamed", "Snapshot"),
        # 参考地址： https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventDefinedDetailType
        ("Removed", "Renamed"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventUndefinedDetailType
        ("Booted", "Migrated", "Restored", "Snapshot", "Wakeup"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventStartedDetailType
        ("Paused", "Migrated", "IOError", "Watchdog", "Restored", "Snapshot", "API error"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventSuspendedDetailType
        ("Unpaused", "Migrated", "Snapshot"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventResumedDetailType
        ("Shutdown", "Destroyed", "Crashed", "Migrated", "Saved", "Failed", "Snapshot"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventStoppedDetailType
        ("Finished", "Guest", "Host"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventShutdownDetailType
        ("Memory", "Disk"),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventPMSuspendedDetailType
        ("Panicked",),
        # 参考地址：https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventCrashedDetailType
        )
    return dom_event_strings[event][detail]


vir_event_loop_poll_start()
