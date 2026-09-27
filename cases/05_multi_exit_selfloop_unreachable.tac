entry:
  x = 0
  goto spin
spin:
  x = x + 1
  if x < 100 goto spin else goto check
check:
  if x == 100 goto exit1 else goto exit2
exit1:
  r = 1
  return
exit2:
  r = 2
  return
dead1:
  q = 9
  goto dead2
dead2:
  q = q + 1
  return
