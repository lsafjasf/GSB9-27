entry:
  x = x + 1
  if x < 3 goto entry else goto body
body:
  y = y + 1
  if y < 5 goto entry else goto exit
exit:
  return
