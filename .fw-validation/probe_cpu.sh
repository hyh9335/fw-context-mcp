#!/bin/bash
ps aux | grep "cli index" | grep -v grep | awk '{print $2, $3"%CPU", $10"TIME", $12}'
echo ---
free -g | head -2
