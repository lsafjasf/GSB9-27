entry:
  i = 0
  goto outer_head
outer_head:
  if i < 10 goto outer_body else goto exit
outer_body:
  j = 0
  goto inner_head
inner_head:
  if j < 10 goto inner_body else goto outer_next
inner_body:
  j = j + 1
  goto inner_head
outer_next:
  i = i + 1
  goto outer_head
exit:
  return
