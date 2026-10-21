"""Bounded Linux execution of REVIEWED collector code, not a security sandbox.

No shell, exec, pickle, ambient credential lookup or retry. The private pipe
carries a bounded byte result, never a credential or native original body.
Only a single-threaded Linux parent is supported. All I/O is supervised by a
real monotonic deadline, independently of any injected evidence clock.
"""
import math
import os
import selectors
import signal
import sys
import threading
import time


class FinishWorkerError(RuntimeError):
    """Fixed codes only; child errors/output are never diagnostics."""


def supported():
    return (sys.platform == 'linux' and hasattr(os, 'fork') and hasattr(os, 'waitid')
            and threading.current_thread() is threading.main_thread()
            and threading.active_count() == 1
            and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)


def _child(read_fd, write_fd, work, maximum):
    try:
        os.close(read_fd)
        os.setsid()
        # No output or inherited store/credential-channel descriptors. Fresh
        # source readers open their own bounded GET connections in this child.
        null = os.open('/dev/null', os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        for name in os.listdir('/proc/self/fd'):
            fd = int(name)
            if fd > 2 and fd != write_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass
        value = work()
        if type(value) is not bytes or not 0 < len(value) <= maximum:
            os._exit(2)
        offset = 0
        while offset < len(value):
            count = os.write(write_fd, value[offset:])
            if count <= 0:
                os._exit(2)
            offset += count
        os.close(write_fd)
        os._exit(0)
    except BaseException:
        os._exit(2)


def _cleanup(pid, deadline):
    # Keep the leader unreaped (WNOWAIT below) until group cleanup. Its PID is
    # still reserved, so a later killpg cannot target a recycled process group.
    clean = True
    for kill, target in ((os.killpg, pid), (os.kill, pid)):
        try:
            kill(target, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            clean = False
    while True:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except OSError:
            return False
        if done == pid:
            return clean
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.005, left))


def run_once(work, *, timeout, max_bytes=32768):
    """Execute one trusted read-only collector, or fail with bounded cleanup.

    timeout INCLUDES kill/reap time. No thread fallback on unsupported systems.
    Normal EOF alone is insufficient: clean leader exit is also mandatory.
    Process-group cleanup is not containment of deliberately escaping code, nor
    does killing a callback undo any external side effects it already made.
    """
    if not (callable(work) and type(timeout) in (int, float)
            and math.isfinite(timeout) and 0.05 <= timeout <= 120
            and type(max_bytes) is int and 0 < max_bytes <= 32768):
        raise FinishWorkerError('FINISH_WORKER_CONFIGURATION_INVALID')
    if not supported():
        raise FinishWorkerError('FINISH_WORKER_PLATFORM_UNSUPPORTED')
    started = time.monotonic()
    end = started + timeout
    work_end = end - min(1.0, timeout / 4)
    read_fd = write_fd = pid = selector = None
    try:
        read_fd, write_fd = os.pipe()
        if min(read_fd, write_fd) < 3:
            raise FinishWorkerError('FINISH_WORKER_DESCRIPTOR_INVALID')
        pid = os.fork()
        if pid == 0:
            _child(read_fd, write_fd, work, max_bytes)
            os._exit(2)
        os.close(write_fd)
        write_fd = None
        os.set_blocking(read_fd, False)
        selector = selectors.DefaultSelector()
        selector.register(read_fd, selectors.EVENT_READ)
        data, eof = bytearray(), False
        while True:
            left = work_end - time.monotonic()
            if left <= 0:
                raise FinishWorkerError('FINISH_WORKER_TIMEOUT')
            # WNOWAIT observes without reaping; cleanup owns the only waitpid.
            state = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if state is not None and (state.si_code != os.CLD_EXITED or state.si_status != 0):
                raise FinishWorkerError('FINISH_WORKER_FAILED')
            if eof and state is not None:
                if not data:
                    raise FinishWorkerError('FINISH_WORKER_EMPTY')
                return bytes(data)
            if eof:
                time.sleep(min(0.005, left))
                continue
            if not selector.select(min(0.05, left)):
                continue
            try:
                block = os.read(read_fd, min(8192, max_bytes + 1 - len(data)))
            except BlockingIOError:
                continue
            if not block:
                eof = True
            else:
                data.extend(block)
                if len(data) > max_bytes:
                    raise FinishWorkerError('FINISH_WORKER_OVERFLOW')
    except FinishWorkerError:
        raise
    except Exception:
        raise FinishWorkerError('FINISH_WORKER_FAILED') from None
    finally:
        clean = True
        if selector is not None:
            try:
                selector.close()
            except OSError:
                clean = False
        for fd in (read_fd, write_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    clean = False
        if pid is not None and pid > 0:
            clean = _cleanup(pid, end) and clean
        if not clean:
            raise FinishWorkerError('FINISH_WORKER_CLEANUP_FAILED') from None
