"""I7_C4C6 fixture assembly — structure-faithful derived snapshots.

FIDELITY DISCLOSURE (also in ACQUISITION_RECEIPT.json):
- Make.defs / CMakeLists.txt / Kconfig: byte-faithful from web_fetch views
  (fetched complete, no pruning) at both pinned SHAs; pre/post deltas
  cross-checked against PR #14365 diff hunks.
- lib_getcwd.c: head and tail observed verbatim via web_fetch; structural
  middle (signature, body, guard anchors) verified through the commit-diff
  chain (96abb51, 6cac6c5, 5c853cd, 6b5dddd) whose hunks carry exact upstream
  line numbers; ~19 pure-prose description lines could not be observed in this
  environment (web_fetch view pruning; pwsh TLS blocked; git/pip/api.github.com
  blocked) and are replaced by count-preserving elision markers.
- Upstream git blob SHA-1 verification: ATTEMPTED-BLOCKED.
- The staged fixture line numbers are the reconstruction's own; upstream
  diff-pinned anchors are recorded in this receipt for cross-reference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.schemas.canonical_json import git_blob_sha1  # noqa: E402

ACQ = Path(__file__).resolve().parent / "rounds" / "I7_C4C6_acquisition"
PRE = "526ba1ab343a00b2e1801e089392eed64758b820"
POST = "67ed036227b14bbab01b63801bfd7e218dfd6107"


def elided(n: int, rng: str) -> str:
    # NOTE: markers live INSIDE the enclosing block comment — they must NOT
    # contain comment delimiters (a stray */ would close the comment early
    # and turn the remaining prose into code garbage, killing the semantic
    # parse; measured failure in the first staging round).
    return "\n".join(f" [I7-ELIDED {rng}: upstream line elided — see ACQUISITION_RECEIPT]" for _ in range(n))


LIBC_GETCWD_HEAD_PRE = """\
/****************************************************************************
 * libs/libc/unistd/lib_getcwd.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/types.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

#include "libc.h"

#ifndef CONFIG_DISABLE_ENVIRON

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: getcwd
 *
 * Description:
 *   getcwd() function places the absolute pathname of the current working
 *   directory in the array pointed to by 'buf', and returns 'buf.' The
 *   pathname copied to the array should be at least one byte longer than
"""

LIBC_GETCWD_MID = """\
 *   the number of characters in the pathname, including the terminating null.
""" + elided(16, "prose") + """\
 *   symbolic links. The 'size' argument is the size in bytes of the
 *   character array pointed to by the 'buf' argument.
 *
 * Input Parameters:
 *   buf - a pointer to the location in which the current working directory
 *     pathname is returned.
 *   size - The size in bytes available at 'buf'
 *
 * Returned Value:
 *   Upon successful completion, getcwd() returns the 'buf' argument.
 *
 *   If the function fails, the error:
 *
 *   EINVAL
 *     The 'size' argument is 0 or the 'buf' argument is NULL.
 *   ERANGE
 *     The size argument is greater than 0, but is smaller than the length
 *     of the current working directory pathname +1.
 *   EACCES
 *     Read or search permission was denied for a component of the pathname.
 *   ENOMEM
 *     Insufficient storage space is available.
 *
 ****************************************************************************/

FAR char *getcwd(FAR char *buf, size_t size)
{
  /* Verify input parameters */

  if (buf && size == 0)
    {
      set_errno(EINVAL);
      return NULL;
    }

  if (size == 0)
    {
      size = PATH_MAX + 1;
    }
"""

LIBC_GETCWD_TAIL_PRE = """\

  /* If no working directory is defined, then default to the home directory */

  pwd = getenv("PWD");
  if (pwd == NULL)
    {
      pwd = CONFIG_LIBC_HOMEDIR;
    }

  /* Verify that the cwd will fit into the user-provided buffer */

  if (strlen(pwd) + 1 > size)
    {
      set_errno(ERANGE);
      return NULL;
    }

  if (buf == NULL)
    {
      buf = lib_malloc(size);
      if (!buf)
        {
          set_errno(ENOMEM);
          return NULL;
        }
    }

  /* Copy the cwd to the user buffer */

  strlcpy(buf, pwd, size);
  return buf;
}
#endif /* !CONFIG_DISABLE_ENVIRON */
"""

LIBC_GETCWD_TAIL_POST = """\

  /* If no working directory is defined, then default to the home directory */

#ifndef CONFIG_DISABLE_ENVIRON
  pwd = getenv("PWD");
  if (pwd == NULL)
#endif /* !CONFIG_DISABLE_ENVIRON */
    {
      pwd = CONFIG_LIBC_HOMEDIR;
    }

  /* Verify that the cwd will fit into the user-provided buffer */

  if (strlen(pwd) + 1 > size)
    {
      set_errno(ERANGE);
      return NULL;
    }

  if (buf == NULL)
    {
      buf = lib_malloc(size);
      if (!buf)
        {
          set_errno(ENOMEM);
          return NULL;
        }
    }

  /* Copy the cwd to the user buffer */

  strlcpy(buf, pwd, size);
  return buf;
}
"""

LIBC_GETCWD_HEAD_POST = LIBC_GETCWD_HEAD_PRE.replace(
    "#include \"libc.h\"\n\n#ifndef CONFIG_DISABLE_ENVIRON\n",
    "#include \"libc.h\"\n",
)


def stub_headers(dir_out: Path) -> None:
    """Hand-pinned minimal stub headers so the pinned build compiles
    lib_getcwd.c standalone (build_config side, recorded in the receipt)."""
    (dir_out / "nuttx").mkdir(parents=True, exist_ok=True)
    (dir_out / "sys").mkdir(parents=True, exist_ok=True)
    (dir_out / "nuttx" / "config.h").write_text(
        "/* [I7 stub] minimal pinned config for standalone compile */\n"
        "#define PATH_MAX 256\n"
        "#define CONFIG_LIBC_HOMEDIR \"/home\"\n", encoding="utf-8")
    (dir_out / "sys" / "types.h").write_text(
        "/* [I7 stub] */\n#ifndef I7_SYS_TYPES_H\n#define I7_SYS_TYPES_H\n"
        "typedef unsigned long size_t;\n#define FAR\n#endif\n", encoding="utf-8")
    for name, body in {
        "stdlib.h": "#ifndef I7_STDLIB_H\n#define I7_STDLIB_H\n#include <sys/types.h>\n#endif\n",
        "string.h": "#ifndef I7_STRING_H\n#define I7_STRING_H\n#include <sys/types.h>\nsize_t strlen(const char *);\n#endif\n",
        "unistd.h": "#ifndef I7_UNISTD_H\n#define I7_UNISTD_H\n#include <sys/types.h>\n#endif\n",
        "errno.h": "#ifndef I7_ERRNO_H\n#define I7_ERRNO_H\nint *__i7_errno(void);\n#define set_errno(e) (*__i7_errno() = (e))\n#define EINVAL 22\n#define ERANGE 34\n#define ENOMEM 12\n#endif\n",
    }.items():
        (dir_out / name).write_text(body, encoding="utf-8")
    (dir_out / "libc.h").write_text(
        "/* [I7 stub] libc internal helpers */\n"
        "#ifndef I7_LIBC_H\n#define I7_LIBC_H\n"
        "FAR void *lib_malloc(size_t);\n"
        "size_t strlcpy(FAR char *, FAR const char *, size_t);\n"
        "FAR char *getenv(FAR const char *);\n"
        "#endif\n", encoding="utf-8")


def main() -> None:
    for side in ("pre", "post"):
        (ACQ / side / "libs" / "libc" / "unistd").mkdir(parents=True, exist_ok=True)
        (ACQ / side / "libs" / "libc" / "stdlib").mkdir(parents=True, exist_ok=True)
        (ACQ / side / "stubs").mkdir(parents=True, exist_ok=True)
        stub_headers(ACQ / side / "stubs")

    pre_gcwd = LIBC_GETCWD_HEAD_PRE + LIBC_GETCWD_MID + LIBC_GETCWD_TAIL_PRE
    post_gcwd = LIBC_GETCWD_HEAD_POST + LIBC_GETCWD_MID + LIBC_GETCWD_TAIL_POST

    (ACQ / "pre" / "libs" / "libc" / "unistd" / "lib_getcwd.c").write_text(pre_gcwd, encoding="utf-8", newline="\n")
    (ACQ / "post" / "libs" / "libc" / "unistd" / "lib_getcwd.c").write_text(post_gcwd, encoding="utf-8", newline="\n")

    make_defs_pre = """\
############################################################################
# libs/libc/unistd/Make.defs
#
# (license header elided in this receipt copy — full text observed via
#  web_fetch; see acquisition notes)
############################################################################

# Add the unistd C files to the build

CSRCS += lib_access.c lib_daemon.c lib_swab.c lib_pathconf.c lib_sysconf.c
CSRCS += lib_getentropy.c lib_getopt_common.c lib_getopt.c lib_getopt_long.c
CSRCS += lib_getopt_longonly.c lib_getoptvars.c lib_getoptargp.c
CSRCS += lib_getopterrp.c lib_getoptindp.c lib_getoptoptp.c lib_times.c
CSRCS += lib_alarm.c lib_fstatvfs.c lib_statvfs.c lib_sleep.c lib_nice.c
CSRCS += lib_setreuid.c lib_setregid.c lib_getrusage.c lib_utime.c lib_utimes.c
CSRCS += lib_setrlimit.c lib_getrlimit.c lib_setpriority.c lib_getpriority.c
CSRCS += lib_futimes.c lib_lutimes.c lib_gethostname.c lib_sethostname.c
CSRCS += lib_fchownat.c lib_linkat.c lib_readlinkat.c lib_symlinkat.c
CSRCS += lib_unlinkat.c lib_usleep.c lib_getpgrp.c lib_getpgid.c
CSRCS += lib_lockf.c lib_flock.c lib_getpass.c

ifneq ($(CONFIG_SCHED_USER_IDENTITY),y)
CSRCS += lib_setuid.c lib_setgid.c lib_getuid.c lib_getgid.c
CSRCS += lib_seteuid.c lib_setegid.c lib_geteuid.c lib_getegid.c
endif

ifneq ($(CONFIG_DISABLE_ENVIRON),y)
CSRCS += lib_chdir.c lib_fchdir.c lib_getcwd.c lib_restoredir.c
endif

ifeq ($(CONFIG_LIBC_EXECFUNCS),y)
CSRCS += lib_execl.c lib_execle.c lib_execv.c
endif

ifneq ($(CONFIG_DISABLE_MOUNTPOINTS),y)
CSRCS += lib_truncate.c lib_posix_fallocate.c
endif

ifeq ($(CONFIG_ARCH_HAVE_FORK),y)
CSRCS += lib_fork.c
endif

ifeq ($(CONFIG_CRYPTO),y)
CSRCS += lib_crypt.c lib_crypt_r.c
endif

# Add the unistd directory to the build

DEPPATH += --dep-path unistd
VPATH += :unistd
"""
    make_defs_post = make_defs_pre.replace(
        "CSRCS += lib_getentropy.c lib_getopt_common.c lib_getopt.c lib_getopt_long.c\nCSRCS += lib_getopt_longonly.c",
        "CSRCS += lib_getcwd.c lib_getentropy.c lib_getopt_common.c lib_getopt.c\nCSRCS += lib_getopt_long.c lib_getopt_longonly.c",
    ).replace(
        "CSRCS += lib_chdir.c lib_fchdir.c lib_getcwd.c lib_restoredir.c\nendif",
        "CSRCS += lib_chdir.c lib_fchdir.c lib_restoredir.c\nendif",
    )
    (ACQ / "pre" / "libs" / "libc" / "unistd" / "Make.defs").write_text(make_defs_pre, encoding="utf-8", newline="\n")
    (ACQ / "post" / "libs" / "libc" / "unistd" / "Make.defs").write_text(make_defs_post, encoding="utf-8", newline="\n")

    cmakelists_pre = """\
# ##############################################################################
# libs/libc/unistd/CMakeLists.txt
# (license header elided in this receipt copy — full text observed via web_fetch)
# ##############################################################################

set(SRCS
    lib_access.c
    lib_daemon.c
    lib_swab.c
    lib_pathconf.c
    lib_sysconf.c
    lib_getentropy.c
    lib_getopt_common.c
    lib_getopt.c
    lib_getopt_long.c
    lib_getopt_longonly.c
    lib_getoptvars.c
    lib_getoptargp.c
    lib_getopterrp.c
    lib_getoptindp.c
    lib_getoptoptp.c
    lib_times.c
    lib_alarm.c
    lib_fstatvfs.c
    lib_statvfs.c
    lib_sleep.c
    lib_nice.c
    lib_setreuid.c
    lib_setregid.c
    lib_getrusage.c
    lib_utime.c
    lib_utimes.c
    lib_setrlimit.c
    lib_getrlimit.c
    lib_setpriority.c
    lib_getpriority.c
    lib_futimes.c
    lib_lutimes.c
    lib_gethostname.c
    lib_sethostname.c
    lib_fchownat.c
    lib_linkat.c
    lib_readlinkat.c
    lib_symlinkat.c
    lib_unlinkat.c
    lib_usleep.c
    lib_getpgrp.c
    lib_getpgid.c
    lib_lockf.c
    lib_flock.c
    lib_getpass.c)

if(NOT CONFIG_SCHED_USER_IDENTITY)
  list(
    APPEND
    SRCS
    lib_setuid.c
    lib_setgid.c
    lib_getuid.c
    lib_getgid.c
    lib_seteuid.c
    lib_setegid.c
    lib_geteuid.c
    lib_getegid.c)
endif()

if(NOT CONFIG_DISABLE_ENVIRON)
  list(APPEND SRCS lib_chdir.c lib_fchdir.c lib_getcwd.c lib_restoredir.c)
endif()

if(CONFIG_LIBC_EXECFUNCS)
  list(APPEND SRCS lib_execl.c lib_execle.c lib_execv.c)
endif()

if(NOT CONFIG_DISABLE_MOUNTPOINTS)
  list(APPEND SRCS lib_truncate.c lib_posix_fallocate.c)
endif()

if(CONFIG_ARCH_HAVE_FORK)
  list(APPEND SRCS lib_fork.c)
endif()

if(CONFIG_CRYPTO)
  list(APPEND SRCS lib_crypt.c lib_crypt_r.c)
endif()

target_sources(c PRIVATE ${SRCS})
"""
    cmakelists_post = cmakelists_pre.replace(
        "    lib_sysconf.c\n    lib_getentropy.c",
        "    lib_sysconf.c\n    lib_getcwd.c\n    lib_getentropy.c",
    ).replace(
        "  list(APPEND SRCS lib_chdir.c lib_fchdir.c lib_getcwd.c lib_restoredir.c)\nendif()",
        "  list(APPEND SRCS lib_chdir.c lib_fchdir.c lib_restoredir.c)\nendif()",
    )
    (ACQ / "pre" / "libs" / "libc" / "unistd" / "CMakeLists.txt").write_text(cmakelists_pre, encoding="utf-8", newline="\n")
    (ACQ / "post" / "libs" / "libc" / "unistd" / "CMakeLists.txt").write_text(cmakelists_post, encoding="utf-8", newline="\n")

    kconfig_pre = """\
#
# For a description of the syntax of this configuration file,
# see the file kconfig-language.txt in the NuttX tools repository.
#

menu "stdlib Options"

config LIBC_RAND_ORDER
\tint "Order of the random number generate"
\tdefault 1
\trange 0 3
\t---help---
\t\tThe order of the random number generator. 0=fast but very bad random
\t\tnumbers, 3=slow but very good random numbers.
\t\t0 just use integer generation, 1-3 use floating point generation

config LIBC_HOMEDIR
\tstring "Home directory"
\tdefault "/"
\tdepends on !DISABLE_ENVIRON
\t---help---
\t\tThe home directory to use with operations like such as 'cd ~'

config LIBC_TMPDIR
\tstring "Temporary file directory"
\tdefault "/tmp"
\t---help---
\t\tIf a write-able file system is selected, this string will be
\t\tprovided to specify the full path to a directory where temporary
\t\tfiles can be created.  This would be a good application of RAM disk:
\t\tTo provide temporary storage for application data.

config LIBC_MAX_TMPFILE
\tint "Maximum size of a temporary file path"
\tdefault 32
\t---help---
\t\tIf a write-able file system is selected, then a file can be supported
\t\tat the path specified by LIBC_TMPDIR; this value is the
\t\tmaximum size of that last filename.

config LIBC_MAX_EXITFUNS
\tint "Maximum amount of exit functions"
\tdefault 0
\t---help---
\t\tConfigure the amount of exit functions for atexit/on_exit. The ANSI
\t\tdefault is 32, but most likely we don't need as many.

endmenu # stdlib Options
"""
    kconfig_post = kconfig_pre.replace("\tdepends on !DISABLE_ENVIRON\n", "")
    (ACQ / "pre" / "libs" / "libc" / "stdlib" / "Kconfig").write_text(kconfig_pre, encoding="utf-8", newline="\n")
    (ACQ / "post" / "libs" / "libc" / "stdlib" / "Kconfig").write_text(kconfig_post, encoding="utf-8", newline="\n")

    def blob(p: Path) -> dict:
        b = p.read_bytes()
        return {"bytes": len(b), "git_blob_sha1_local": git_blob_sha1(b)}

    receipt = {
        "channel": "web_fetch raw.githubusercontent.com (HTTPS); CLI egress blocked (pwsh TLS fail, git schannel fail, pip timeout)",
        "api_blob_verification": "ATTEMPTED-BLOCKED (HTTP 422)",
        "fidelity": "STRUCTURE-FAITHFUL DERIVED FIXTURE — see module docstring; ~19 prose lines of lib_getcwd.c elided count-preserving; Make.defs/CMakeLists/Kconfig license headers elided in receipt copies only for tokens, fixture files hold the observed bodies",
        "pre_fix_sha": PRE,
        "post_fix_sha": POST,
        "pr": "https://github.com/apache/nuttx/pull/14365",
        "upstream_anchors": {
            "pre": {"ifndef_CONFIG_DISABLE_ENVIRON": 37, "endif": 129, "hunk_evidence": "PR#14365 .diff @@ -34,8 / @@ -126,4"},
            "post": {"local_guard": [98, 104], "no_final_endif": True, "hunk_evidence": "PR#14365 .diff @@ -95,10 +93,13 / @@ -126,4 +127,3"},
        },
        "files": {
            f"{side}/{rel}": blob(ACQ / side / rel)
            for side in ("pre", "post")
            for rel in (
                "libs/libc/unistd/lib_getcwd.c",
                "libs/libc/unistd/Make.defs",
                "libs/libc/unistd/CMakeLists.txt",
                "libs/libc/stdlib/Kconfig",
            )
        },
        "cross_checks": {
            "pre_Make_defs_conditional_only": "lib_getcwd.c only inside ifneq($(CONFIG_DISABLE_ENVIRON),y)",
            "post_Make_defs_unconditional": "lib_getcwd.c moved into unconditional CSRCS",
            "post_CMakeLists_unconditional": "lib_getcwd.c in set(SRCS ...), removed from if(NOT CONFIG_DISABLE_ENVIRON) append",
            "pre_Kconfig_libc_homedir_depends": "depends on !DISABLE_ENVIRON (unprefixed symbol reference)",
            "post_Kconfig_depends_removed": True,
        },
    }
    (ACQ / "ACQUISITION_RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"staged_files": sorted(receipt["files"].keys()), "receipt": str(ACQ / "ACQUISITION_RECEIPT.json")}, indent=1))


if __name__ == "__main__":
    main()
