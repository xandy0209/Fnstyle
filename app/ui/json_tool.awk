# json_tool.awk —— 纯 awk 实现的扁平 JSON 对象工具
#
# 用法:
#   awk -f json_tool.awk -v mode=parse  < body.json       # 输出 key<TAB>value
#   awk -f json_tool.awk -v mode=merge  cfg.json kv.tsv    # 合并后输出新 JSON
#
# 约定:
#   - 只处理「扁平对象」（value 为字符串 / 数字 / 布尔 / null）
#   - 字符串值保留原始 JSON 字面量（含首尾引号与转义）
#   - 能正确处理字符串内部的逗号、转义引号、中文

function trim(s) { gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", s); return s }

# JSON 字符串转义（逐字符处理，避免 gsub 替换串的转义歧义）
function esc(s,   i, c, out) {
    out = ""
    for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\") out = out "\\\\"
        else if (c == "\"") out = out "\\\""
        else out = out c
    }
    return out
}

function is_space(c) {
    return (c == " " || c == "\t" || c == "\r" || c == "\n")
}

# 解析扁平 JSON 对象字符串；结果写入全局 KEYS[] / VALS[]；返回键数量
function parse_obj(s,   i, n, c2, kstart, vstart, key, val, cnt) {
    cnt = 0
    n = length(s)
    i = 1
    while (i <= n && (is_space(substr(s,i,1)) || substr(s,i,1) == "{")) i++
    while (i <= n) {
        while (i <= n && (is_space(substr(s,i,1)) || substr(s,i,1) == ",")) i++
        if (i > n) break
        if (substr(s, i, 1) != "\"") break
        i++
        kstart = i
        while (i <= n) {
            c2 = substr(s, i, 1)
            if (c2 == "\\") { i += 2; continue }
            if (c2 == "\"") break
            i++
        }
        key = substr(s, kstart, i - kstart)
        i++
        while (i <= n && substr(s,i,1) != ":") i++
        i++
        while (i <= n && is_space(substr(s,i,1))) i++
        if (i > n) break
        if (substr(s, i, 1) == "\"") {
            vstart = i
            i++
            while (i <= n) {
                c2 = substr(s, i, 1)
                if (c2 == "\\") { i += 2; continue }
                if (c2 == "\"") { i++; break }
                i++
            }
            val = substr(s, vstart, i - vstart)
        } else if (substr(s, i, 1) == "[") {
            # 数组值：扫描到匹配的 ]（支持嵌套 [] 与字符串内的 ]）
            vstart = i
            depth = 0
            inq = 0
            while (i <= n) {
                c2 = substr(s, i, 1)
                if (inq) {
                    if (c2 == "\\") { i += 2; continue }
                    if (c2 == "\"") inq = 0
                    i++
                    continue
                }
                if (c2 == "\"") { inq = 1; i++; continue }
                if (c2 == "[") depth++
                if (c2 == "]") {
                    depth--
                    if (depth == 0) { i++; break }
                }
                i++
            }
            val = substr(s, vstart, i - vstart)
        } else if (substr(s, i, 1) == "{") {
            # 对象值：扫描到匹配的 }
            vstart = i
            depth = 0
            inq = 0
            while (i <= n) {
                c2 = substr(s, i, 1)
                if (inq) {
                    if (c2 == "\\") { i += 2; continue }
                    if (c2 == "\"") inq = 0
                    i++
                    continue
                }
                if (c2 == "\"") { inq = 1; i++; continue }
                if (c2 == "{") depth++
                if (c2 == "}") {
                    depth--
                    if (depth == 0) { i++; break }
                }
                i++
            }
            val = substr(s, vstart, i - vstart)
        } else {
            vstart = i
            while (i <= n && substr(s,i,1) != "," && substr(s,i,1) != "}") i++
            val = trim(substr(s, vstart, i - vstart))
        }
        KEYS[++cnt] = key
        VALS[cnt] = val
    }
    return cnt
}

BEGIN {
    if (mode == "merge") {
        tb = ""
        while ((getline ln < ARGV[1]) > 0) tb = tb ln "\n"
        close(ARGV[1])
        ne = parse_obj(tb)
        while ((getline ln < ARGV[2]) > 0) {
            p = index(ln, "\t")
            if (p == 0) continue
            k = substr(ln, 1, p - 1)
            v = substr(ln, p + 1)
            if (k == "") continue
            found = 0
            for (j = 1; j <= ne; j++) {
                if (KEYS[j] == k) { VALS[j] = v; found = 1; break }
            }
            if (!found) { ne++; KEYS[ne] = k; VALS[ne] = v }
        }
        close(ARGV[2])
        printf "{\n"
        for (j = 1; j <= ne; j++) {
            if (j > 1) printf ",\n"
            kv = esc(KEYS[j])
            vv = VALS[j]
            if (vv == "") vv = "\"\""
            printf "  \"%s\": %s", kv, vv
        }
        printf "\n}\n"
        # 阻止主输入循环继续读取 ARGV 文件
        ARGV[1] = ""; ARGV[2] = ""
        exit 0
    }
}

{ buf = buf $0 "\n" }

END {
    if (mode != "parse") exit 0
    nb = parse_obj(buf)
    for (j = 1; j <= nb; j++) printf "%s\t%s\n", KEYS[j], VALS[j]
}
