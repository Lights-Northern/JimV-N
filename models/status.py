#!/usr/bin/env python
# -*- coding: utf-8 -*-


from enum import IntEnum


__author__ = 'James Iter'
__date__ = '2017/3/22'
__contact__ = 'james.iter.cn@gmail.com'
__copyright__ = '(c) 2017 by James Iter.'


class JimVEdition(IntEnum):
    standalone = 0
    hyper_convergence = 1


class StorageMode(IntEnum):
    local = 0
    shared_mount = 1
    ceph = 2
    glusterfs = 3


class EmitKind(IntEnum):
    log = 0
    guest_event = 1
    host_event = 2
    response = 3
    guest_collection_performance = 4
    host_collection_performance = 5


class GuestState(IntEnum):
    # 参考地址：
    # http://libvirt.org/docs/libvirt-appdev-guide-python/en-US/html/libvirt_application_development_guide_using_python-Guest_Domains-Information-State.html

    no_state = 0
    booting = 1
    running = 2
    blocked = 3
    paused = 4
    shutdown = 5
    shutoff = 6
    crashed = 7
    pm_suspended = 8
    migrating = 9
    update = 10
    creating = 11
    snapshot_converting = 12
    dirty = 255


class HostEvent(IntEnum):
    heartbeat = 0


class LogLevel(IntEnum):
    critical = 0
    error = 1
    warn = 2
    info = 3
    debug = 4


class ResponseState(IntEnum):
    success = True
    failure = False


class OSTemplateInitializeOperateKind(IntEnum):
    cmd = 0
    write_file = 1
    append_file = 2


class GuestCollectionPerformanceDataKind(IntEnum):
    cpu_memory = 0
    traffic = 1
    disk_io = 2


class HostCollectionPerformanceDataKind(IntEnum):
    cpu_memory = 0
    traffic = 1
    disk_usage_io = 2

