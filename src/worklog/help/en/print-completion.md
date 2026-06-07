---
title: print-completion — shell tab-completion
category: command
see_also: admin, alias
---
`wl print-completion <fish|bash|zsh>` prints a completion script. Write it to your shell rc
once and new shells load it (it stays in sync with the commands, and completes node ids,
tags, dates, and `wl help` topics).
  fish:  wl print-completion fish | source
  bash:  eval "$(wl print-completion bash)"
  zsh:   eval "$(wl print-completion zsh)"
See `wl help admin`.
