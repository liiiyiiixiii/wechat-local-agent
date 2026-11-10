"""Windows-only primitives. Pipes reject remote clients and grant only the current SID access."""

import hashlib
import time
import os
import threading

import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32security as security
import msvcrt

from .config import HOME


def user_sid():
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    return security.GetTokenInformation(token, security.TokenUser)[0]


def private_acl(flags=0):
    acl = security.ACL()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, win32con.GENERIC_ALL, user_sid())
    return acl


def protect_directory(path):
    acl = private_acl(win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE)
    security.SetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


def pipe_name():
    identity = security.ConvertSidToStringSid(user_sid()) + os.path.normcase(str(HOME))
    return r'\\.\pipe\wxlocal-' + hashlib.sha256(identity.encode()).hexdigest()[:32]


def open_pipe():
    name = pipe_name()
    win32pipe.WaitNamedPipe(name, 5000)
    handle = win32file.CreateFile(
        name,
        win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        0,
        None,
        win32con.OPEN_EXISTING,
        win32con.SECURITY_SQOS_PRESENT | (security.SecurityIdentification << 16),
        None,
    )
    try:
        owner = security.GetSecurityInfo(
            handle, security.SE_KERNEL_OBJECT, security.OWNER_SECURITY_INFORMATION
        ).GetSecurityDescriptorOwner()
        if owner != user_sid():
            raise PermissionError('unexpected_pipe_owner')
        return os.fdopen(msvcrt.open_osfhandle(handle.Detach(), os.O_RDWR | os.O_BINARY), 'r+b', buffering=0)
    finally:
        handle.Close()


class PipeReader:
    """Read one bounded JSON frame without letting a stalled peer hold a worker."""

    def __init__(self, stream, timeout):
        self.stream = stream
        self.timeout = timeout

    def readline(self, limit):
        deadline = time.monotonic() + self.timeout
        handle = msvcrt.get_osfhandle(self.stream.fileno())
        data = bytearray()
        while len(data) < limit:
            if time.monotonic() >= deadline:
                raise TimeoutError('pipe_timeout')
            _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
            if not available:
                time.sleep(0.01)
                continue
            chunk = self.stream.read(min(available, limit - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if b'\n' in chunk:
                break
        return bytes(data)


def serve_pipe(exchange, stop):
    sd = security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorOwner(user_sid(), False)
    sd.SetSecurityDescriptorDacl(True, private_acl(), False)
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = sd

    slots = threading.BoundedSemaphore(16)

    def handle_one(handle):
        try:
            fd = msvcrt.open_osfhandle(handle.Detach(), os.O_RDWR | os.O_BINARY)
            with os.fdopen(fd, 'r+b', buffering=0) as stream:
                exchange(PipeReader(stream, 15), stream)
        except (OSError, pywintypes.error):
            pass
        finally:
            slots.release()

    while not stop.is_set():
        if not slots.acquire(timeout=1):
            continue
        handle = win32pipe.CreateNamedPipe(
            pipe_name(),
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | 8,
            32,
            65536,
            65536,
            5000,
            attrs,
        )
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as exc:
            if exc.winerror != 535:  # already connected
                handle.Close()
                raise
        threading.Thread(target=handle_one, args=(handle,), daemon=True).start()
