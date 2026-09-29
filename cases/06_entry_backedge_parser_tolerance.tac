# entry has a back edge from body (and the consecutive labels
# entry:/alias: plus "if ... goto L" without an else must parse)
entry:
alias:
  x = 0
  if x < 10 goto body
toexit:
  goto exit
body:
  x = x + 1
  if x < 10 goto body else goto entry
exit:
  return
