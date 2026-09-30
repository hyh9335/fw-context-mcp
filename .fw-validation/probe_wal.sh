#!/bin/bash
ls -la ~/fwctx-v2-work/N1/index/*/index.db* ~/fwctx-v2-work/N2/index/*/index.db* 2>/dev/null | awk '{printf "%s %.2fGB %s\n", $5/1073741824, $5/1073741824, $9}' | cut -c12-
