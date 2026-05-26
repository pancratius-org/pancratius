---
title: Bad fixture
lang: ru
---

This work embeds a machine-local absolute path:

![cover](/Users/lr/x.jpg)

…a machine home path as a link/image target:

![home](~/secret/cover.jpg)

…and a parent-traversal escape out of the content root:

![escape](../../../../outside/asset.png)
