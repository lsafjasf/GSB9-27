# 分级依据表

级别定义：L1=公开；L2=内部；L3=敏感；L4=高度敏感

| 字段 | 级别 | 命中规则（按裁决顺序） | 证据 | 冲突裁决 |
| --- | --- | --- | --- | --- |
| mobile | L3 敏感 | R-PHONE-VALUE→L3(P90) | [R-PHONE-VALUE] 取值命中 3/3（阈值 30%），样例 138***78, 159***32, 187***11（依据：个人信息分级：手机号属敏感个人信息） | 无 |
| id_card_no | L4 高度敏感 | R-ID-VALUE→L4(P100)<br>R-ID-NAME→L4(P60) | [R-ID-VALUE] 取值命中 3/3（阈值 30%），样例 110***2X, 310***34, 440***43（依据：《个人信息保护法》敏感个人信息：身份证件号码）<br>[R-ID-NAME] 字段名命中 /id_?card/（依据：按字段语义推定：身份证件号属高度敏感（可被取值证据推翻）） | 无 |
| bank_card | L4 高度敏感 | R-BANK-VALUE→L4(P98) | [R-BANK-VALUE] 取值命中 2/2（阈值 30%），样例 622***22, 622***83（依据：金融数据安全分级指南：银行卡号属高度敏感） | 无 |
| remark | L4 高度敏感 | R-ID-VALUE→L4(P100)<br>R-EMAIL-VALUE→L3(P88) | [R-ID-VALUE] 取值命中 2/5（阈值 30%），样例 110***2X, 310***34（依据：《个人信息保护法》敏感个人信息：身份证件号码）<br>[R-EMAIL-VALUE] 取值命中 2/5（阈值 30%），样例 a.b***om, lis***cn（依据：个人信息分级：邮箱属敏感个人信息） | R-ID-VALUE(L4) 胜过 R-EMAIL-VALUE(L3)：按优先级表裁决：R-ID-VALUE（优先级 100）高于 R-EMAIL-VALUE（优先级 88） |
| user_idcard | L2 内部 | R-EMP-CODE-VALUE→L2(P65)<br>R-ID-NAME→L4(P60) | [R-EMP-CODE-VALUE] 取值命中 3/3（阈值 50%），样例 E***, E***, E***（依据：内部编码仅用于组织内部，定为内部级）<br>[R-ID-NAME] 字段名命中 /id_?card/（依据：按字段语义推定：身份证件号属高度敏感（可被取值证据推翻）） | R-EMP-CODE-VALUE(L2) 胜过 R-ID-NAME(L4)：按优先级表裁决：R-EMP-CODE-VALUE（优先级 65）高于 R-ID-NAME（优先级 60） |
| contact_info | L3 敏感 | R-PHONE-VALUE→L3(P90)<br>R-EMAIL-VALUE→L3(P88) | [R-PHONE-VALUE] 取值命中 1/3（阈值 30%），样例 138***11（依据：个人信息分级：手机号属敏感个人信息）<br>[R-EMAIL-VALUE] 取值命中 2/3（阈值 30%），样例 zha***om, lis***cn（依据：个人信息分级：邮箱属敏感个人信息） | 无 |
| real_name | L3 敏感 | R-NAME-PII→L3(P55) | [R-NAME-PII] 字段名命中 /real_?name/（依据：个人信息分级：姓名属敏感个人信息） | 无 |
| company_address | L1 公开 | R-SOURCE-PUBLIC→L1(P58)<br>R-ADDR-NAME→L3(P55) | [R-SOURCE-PUBLIC] 来源命中「官网公开页」（依据：已公开信息定为公开级（来源证据优先于字段名，弱于取值证据））<br>[R-ADDR-NAME] 字段名命中 /address/（依据：个人信息分级：住址属敏感个人信息） | R-SOURCE-PUBLIC(L1) 胜过 R-ADDR-NAME(L3)：按优先级表裁决：R-SOURCE-PUBLIC（优先级 58）高于 R-ADDR-NAME（优先级 55） |
| order_status | L1 公开 | R-STATUS-NAME→L1(P10) | [R-STATUS-NAME] 字段名命中 /status/（依据：通用枚举字段不含个人信息，定为公开级） | 无 |
| score | L2 内部 | （无） | 无规则命中，按兜底策略定为内部级 | 无 |
| email | L2 内部 | （无） | 无规则命中，按兜底策略定为内部级 | 无 |
| id_card_remark | L4 高度敏感 | R-ID-VALUE→L4(P100)<br>R-ID-NAME→L4(P60) | [R-ID-VALUE] 取值命中 1/3（阈值 30%），样例 110***2X（依据：《个人信息保护法》敏感个人信息：身份证件号码）<br>[R-ID-NAME] 字段名命中 /id_?card/（依据：按字段语义推定：身份证件号属高度敏感（可被取值证据推翻）） | 无 |
| status | L1 公开 | R-SOURCE-PUBLIC→L1(P58)<br>R-STATUS-NAME→L1(P10) | [R-SOURCE-PUBLIC] 来源命中「官网公开页」（依据：已公开信息定为公开级（来源证据优先于字段名，弱于取值证据））<br>[R-STATUS-NAME] 字段名命中 /status/（依据：通用枚举字段不含个人信息，定为公开级） | 无 |
