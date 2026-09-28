"""Kill the Codex process tree if the worker exits/crashes (Windows Job Object)."""
import ctypes
from ctypes import wintypes
import os


class Job:
    def __enter__(self):
        self.handle=None
        if os.name!='nt':
            return self
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
                ('flags',wintypes.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),
                ('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(key,ctypes.c_ulonglong) for key in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                      ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits=Limits()
        limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.__exit__()
            raise OSError('Cannot configure process containment')
        return self

    def assign(self,process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            process.kill()
            process.wait()
            raise OSError('Cannot contain Codex; refusing uncontained execution')

    def __exit__(self,*args):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle=None
