---
title: wl — a local worklog & planner
category: guide
see_also: para, planning, node, status, add, day
---
worklog (`wl`) is a fast, local, SQLite-backed worklog and planner. You track
tasks/projects, log progress, plan your day, and review — all from the shell.

Getting started:
  wl init                          create the database (once)
  wl add "ship the Q3 report"      add a task
  wl log 1 "drafted the intro"     log progress on task #1 (auto TODO → DOING)
  wl done 1                        mark it done
  wl day                           today's plan + activity ·  wl tree   the big picture

How help works here:
  • `wl <command> -h`   quick reference for one command (usage + a few examples).
  • `wl help <topic>`   this browser — a fuller explanation of a command, concept,
    parameter, or workflow, with "See also" links to related topics.
  • `wl help --all`     every topic, grouped by category.

Start with `wl help para` (how to organize) or `wl help planning` (goals/summaries
rhythm); `wl help --all` lists the rest.
