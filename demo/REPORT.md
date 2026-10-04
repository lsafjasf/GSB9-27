## 判定表（1.0.0 -> 1.0.0）

总体结论：**破坏性**

| 函数 | 判定项 | 影响等级 | 对象 | 说明 | 依据 |
| --- | --- | --- | --- | --- | --- |
| greet | PARAM_DEFAULT_CHANGED | 低风险/需关注 | punctuation | 参数 punctuation 默认值 '!' -> '.' | 签名仍然兼容（参数仍可选、类型未变），但省略该参数的存量调用会得到不同行为，属于静默行为变化，需要单独评估。 |
| get_user | RETURN_EXTENDED | 低风险/需关注 | email | 返回结构新增字段 email（类型 str） | 新增字段不会破坏读取旧字段的调用方，但严格断言完整返回结构（如 schema 全等校验、位置解包）的调用会受影响，需单独确认。 |
| resize | PARAM_REMOVED_OPTIONAL | 破坏性 | fmt | 删除参数 fmt（可选） | 位置传参的调用可能错位；关键字传该参数的调用会因意外关键字报错。静态判定一律视为破坏性，运行时测试再确认是否误报。 |
| upload | PARAM_ADDED_OPTIONAL | 兼容 | quality | 新增可选参数 quality（类型 int，默认 80） | 新参数带默认值，旧调用不传时行为与旧版本一致（以实现遵守默认值为前提，由实际调用测试复核）。 |
| connect | PARAM_ADDED_REQUIRED | 破坏性 | port | 新增必填参数 port（类型 int） | 旧调用不传该参数，新版本会因缺参报错。 |
| legacy | FUNCTION_REMOVED | 破坏性 | legacy | 删除函数 legacy | 所有对该函数的存量调用在新版本中直接失败。 |

## 实际调用对照表

| 用例 | 函数 | 结果 | 旧版本 | 新版本 | 备注 |
| --- | --- | --- | --- | --- | --- |
| c1 | ping | 结果一致 | 'pong:hi' | 'pong:hi' | 完全兼容：接口与行为均未变 |
| c2 | greet | 结果不同 | 'hello ann!' | 'hello ann.' | 默认值变化：省略 punctuation 时行为不同 |
| c3 | get_user | 结果不同 | {'id': 7, 'name': 'user7'} | {'id': 7, 'name': 'user7', 'email': 'user7@example.com'} | 返回结构扩展：新版多出 email 字段 |
| c4 | resize | 结果一致 | {'path': 'a.png', 'width': 80, 'fmt': 'png'} | {'path': 'a.png', 'width': 80, 'fmt': 'png'} | 删除可选参数：不传 fmt 时仍可正常调用 |
| c5 | resize | 新版报错 | {'path': 'a.png', 'width': 100, 'fmt': 'jpg'} | TypeError: resize() got an unexpected keyword argument 'fmt' | 删除可选参数：仍按旧接口传 fmt 则新版报错 |
| c6 | sum2 | 结果不同 | 4 | 2 | 接口未变但实现回归：静态判兼容、实际结果不同 |
| c7 | legacy | 新版报错 | 'legacy-result' | AttributeError: module 'ifacecheck_impl_new' has no attribute 'legacy' | 删除函数：新版直接 AttributeError |
| c8 | upload | 结果一致 | {'path': 'f.bin', 'compress': False, 'size': 42} | {'path': 'f.bin', 'compress': False, 'size': 42} | 新增可选参数：旧调用不受影响 |
| c9 | connect | 新版报错 | 'conn://db.local' | TypeError: connect() missing 1 required positional argument: 'port' | 新增必填参数：旧调用缺参报错 |

## 静态判定 vs 实际调用：不一致条目清单

| 用例 | 函数 | 不一致类型 | 说明 |
| --- | --- | --- | --- |
| c4 | resize | 误报 | 静态判定为破坏性，但该用例在新版本调用成功且结果一致（可能仅影响其他调用方式，如位置传参）。 |
| c6 | sum2 | 可疑 | 静态判定为兼容，但实际行为已变化（新增键=[] 缺失键=[]）。 |
