"""Wait for a captured owned subtree; never signal or discover unrelated PIDs."""
import psutil


class CleanupUnconfirmed(TimeoutError):
    """An owned effect may still exist; automatic replay is unsafe."""


def capture(process):
    if process is None or type(process.pid) is not int or process.returncode is not None:
        return []
    try:
        root = psutil.Process(process.pid)
        # Pin identity before children(): psutil checks this identity when
        # enumerating descendants. Retain Process objects through their waits.
        root.create_time()
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def wait(processes, timeout):
    """Root exit/Job handle close can precede console-host retirement."""
    _, remaining = psutil.wait_procs(processes, timeout=timeout)
    if remaining:
        raise CleanupUnconfirmed('Owned process cleanup unconfirmed: ' +
                           ', '.join(str(p.pid) for p in remaining))
