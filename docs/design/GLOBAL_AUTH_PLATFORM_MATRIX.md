# Authentication platform contract

Authentication is mandatory for HTTP/WS on each platform; platform status never bypasses it.

| Contract | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Password scrypt / atomic verifier / hashed durable sessions | Implemented; owned native tests | Native Linux storage contract passed; full app target execution pending | Shared implementation; target execution pending |
| Cross-process transaction lock | Named Mutex, lazy Win32 import; native contract tested | flock, lazy fcntl import; native independent-process contract passed | flock, lazy fcntl import; target execution pending |
| Original machine IPC | Existing Named Pipe | Existing Unix socket | Existing Unix socket |
| HTTP/WS default deny, CSRF and revocation | Shared ASGI; real HTTP/WS tested | Shared ASGI; target execution pending | Shared ASGI; target execution pending |
| Production browser / stdio / Job original notice | Owned instance tested | Target execution pending | Target execution pending |

`tests/test_global_auth_platform.py` runs the same independent-process and profile-boundary contracts on each OS. `tests/test_global_auth.py` is portable and inventories actual app HTTP routes. The dedicated CI workflow imports the server, collects tests and runs these contracts on all three systems. Local Windows results are not evidence of remote CI or POSIX execution. Existing IPC platform implementation remains authoritative; no new scheduler, owner or receipt protocol is introduced.

Storage/lock failures propagate as authentication unavailability, never anonymous access. Unsupported OS storage primitives fail explicitly. Windows files are created with restricted mode requested from the OS, using the existing profile/OS-user boundary; POSIX verifies 0600. This change does not claim to sandbox other processes running as the same OS user.

`auth.persistence` declares the implemented three-platform primitive. Windows and WSL Ubuntu native-filesystem storage contracts passed. macOS and full three-platform app CI remain unexecuted. DrvFS without POSIX metadata failed the 0600 contract and is rejected; keep POSIX auth state on a filesystem that enforces private modes. No WSL or host mount settings were changed.
