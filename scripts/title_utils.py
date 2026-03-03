"""UTF-16 标题长度计算，对应 Go pkg/xhsutil/title.go。"""


def calc_title_length(s: str) -> int:
    """计算小红书标题长度。

    规则：非 ASCII 字符（中文、全角符号等）算 2 字节，
    ASCII 字符算 1 字节，最终结果向上取整除以 2。

    Examples:
        >>> calc_title_length("你好世界")
        4
        >>> calc_title_length("hello")
        3
        >>> calc_title_length("OOTD穿搭分享")
        6
    """
    byte_len = 0
    # 用 UTF-16 编码来处理（包括 surrogate pairs）
    encoded = s.encode("utf-16-le")
    for i in range(0, len(encoded), 2):
        code_unit = int.from_bytes(encoded[i : i + 2], "little")
        if code_unit > 127:
            byte_len += 2
        else:
            byte_len += 1
    return (byte_len + 1) // 2
