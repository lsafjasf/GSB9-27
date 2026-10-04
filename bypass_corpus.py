"""绕过尝试语料。

每条：(类别, 目标规则, 变形说明, 原始输入, 期望结果)
期望结果为 "reject" 表示必须被拒绝；
为 ("accept", 规范形式) 表示编码等价写法，规范化后按规范形式接受。
"""

TRAVERSAL = "路径穿越"
ENCODING = "编码变形"
NULL = "空字节/控制字符"
HOMOGLYPH = "大小写/同形字"
EMAIL = "邮箱结构"
FILENAME = "文件名"
NUMBER = "数字"
JSONCAT = "JSON 嵌套结构"

reject = "reject"


def accept(canonical):
    return ("accept", canonical)


CORPUS = [
    # --- 路径穿越：同一种攻击 payload 的各种编码写法 ---
    (TRAVERSAL, "rel_path", "明文 ../", "../etc/passwd", reject),
    (TRAVERSAL, "rel_path", "百分号编码", "%2e%2e%2fetc%2fpasswd", reject),
    (TRAVERSAL, "rel_path", "大写十六进制", "%2E%2E%2Fetc%2Fpasswd", reject),
    (TRAVERSAL, "rel_path", "双重编码", "%252e%252e%252fetc%252fpasswd", reject),
    (TRAVERSAL, "rel_path", "三重编码", "%25252e%25252e%25252fetc", reject),
    (TRAVERSAL, "rel_path", "超长 UTF-8 编码斜杠", "..%c0%afetc", reject),
    (TRAVERSAL, "rel_path", "全角斜杠（百分号编码）", "..%ef%bc%8fetc", reject),
    (TRAVERSAL, "rel_path", "全角点 + 全角斜杠", "\uff0e\uff0e\uff0fetc", reject),
    (TRAVERSAL, "rel_path", "HTML 实体编码点", "&#46;&#46;/etc", reject),
    (TRAVERSAL, "rel_path", "嵌套 HTML 实体", "&amp;#46;&amp;#46;/etc", reject),
    (TRAVERSAL, "rel_path", "实体再做百分号编码", "%26%2346%3B%26%2346%3B/etc", reject),
    (TRAVERSAL, "rel_path", "中段穿越", "a/../../b", reject),
    (TRAVERSAL, "rel_path", "结尾穿越段", "a/b/..", reject),
    (TRAVERSAL, "rel_path", "点开头的段", ".hidden/x", reject),
    (TRAVERSAL, "rel_path", "绝对路径", "/etc/passwd", reject),
    (TRAVERSAL, "rel_path", "全角正斜杠（NFKC 等价写法）", "a\uff0fb", accept("a/b")),

    # --- 空字节 / 控制字符 ---
    (NULL, "username", "尾缀空字节", "admin%00", reject),
    (NULL, "username", "空字节夹带", "admin%00script", reject),
    (NULL, "username", "DEL 字符", "adm\x7fin", reject),
    (NULL, "username", "百分号编码换行", "admin%0a", reject),
    (NULL, "rel_path", "段内空字节", "a%00/../../../etc", reject),

    # --- 大小写 / 同形字 ---
    (HOMOGLYPH, "username", "大写开头", "Admin", reject),
    (HOMOGLYPH, "username", "拉丁扩展字符 ɽ 冒充字母", "adm\xc9\xbdn", reject),
    (HOMOGLYPH, "username", "西里尔 а 冒充 a", "admin".replace("a", "\u0430"), reject),
    (HOMOGLYPH, "username", "全角字母（NFKC 等价）", "\uff41\uff44\uff4d\uff49\uff4e", accept("admin")),
    (HOMOGLYPH, "integer", "全角数字（NFKC 等价）", "\uff11\uff12\uff13", accept("123")),
    (HOMOGLYPH, "slug", "全角连字符（NFKC 等价）", "abc\uff0ddef", accept("abc-def")),

    # --- 邮箱 ---
    (EMAIL, "email", "双 @", "a@b@c.com", reject),
    (EMAIL, "email", "编码第二个 @", "user%40evil.com@x.com", reject),
    (EMAIL, "email", "域名标签以 - 开头", "user@-evil.com", reject),
    (EMAIL, "email", "域名含下划线", "user@evil_host.com", reject),
    (EMAIL, "email", "顶级域含数字", "user@evil.c0m", reject),
    (EMAIL, "email", "实体编码点后仍双 @", "a%40b&#46;c@d.com", reject),
    (EMAIL, "email", "全角 @（NFKC 等价写法）", "user\uff20x.com", accept("user@x.com")),

    # --- 文件名 ---
    (FILENAME, "filename", "穿越前缀编码", "..%2f..%2fetc", reject),
    (FILENAME, "filename", "点开头隐藏文件", ".hidden", reject),
    (FILENAME, "filename", "双扩展名", "a.tar.gz", reject),
    (FILENAME, "filename", "Windows 保留名", "con.txt", reject),
    (FILENAME, "filename", "斜杠夹带", "a/b.txt", reject),
    (FILENAME, "filename", "全角斜杠夹带", "a\uff0fb.txt", reject),

    # --- 数字 ---
    (NUMBER, "integer", "十六进制", "0x1f", reject),
    (NUMBER, "integer", "下划线分隔", "1_000", reject),
    (NUMBER, "integer", "科学计数法", "1e3", reject),
    (NUMBER, "integer", "前导加号", "+123", reject),
    (NUMBER, "integer", "超 19 位", "9999999999999999999999", reject),
    (NUMBER, "integer", "超出 int64 上界", "9223372036854775808", reject),
    (NUMBER, "integer", "超出 int64 下界", "-9223372036854775809", reject),

    # --- JSON 嵌套结构 ---
    (JSONCAT, "json", "嵌套深度 9 层", "[[[[[[[[1]]]]]]]]", reject),
    (JSONCAT, "json", "1001 个节点", "[" + ",".join(["1"] * 1001) + "]", reject),
    (JSONCAT, "json", "大写键名", '{"User":1}', reject),
    (JSONCAT, "json", "编码后变大写键名", '{"%55ser":1}', reject),
    (JSONCAT, "json", "键名含点", '{"user.name":1}', reject),
    (JSONCAT, "json", "NaN 常量", '{"a":NaN}', reject),
    (JSONCAT, "json", "Infinity 常量", '{"a":Infinity}', reject),
    (JSONCAT, "json", "字符串值超长 300", '{"a":"' + "x" * 300 + '"}', reject),
    (JSONCAT, "json", "字符串值藏空字节", '{"a":"x%00"}'.replace("%00", "\x00"), reject),
    (JSONCAT, "json", "深层对象键非法", '{"a":{"b":{"c":{"d":{"e":{".x":1}}}}}}', reject),
]
