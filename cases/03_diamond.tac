entry:
  x = 1
  if x > 0 goto then else goto else
then:
  y = x + 1
  goto join
else:
  y = x - 1
  goto join
join:
  z = y * 2
  return
