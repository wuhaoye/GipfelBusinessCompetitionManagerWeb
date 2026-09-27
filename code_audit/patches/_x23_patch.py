"""X-23：deploy/gipfel.service 移除没人用的 daphne Unix socket（行尾保持与文件一致）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "deploy" / "gipfel.service"

OLD = """# 启动 daphne：HTTP + Socket.IO 同源同端口；Unix socket 放运行目录
ExecStart=__INSTALL_DIR__/backend/.venv/bin/daphne \\
    -u /run/gipfel/gipfel.sock \\
    -b 127.0.0.1 \\
    -p 8000 \\
"""

NEW = """# 启动 daphne：HTTP + Socket.IO 同源同端口，只绑回环，由 nginx 反代
#
# 审计 X-23：改前这里同时监听 Unix socket `/run/gipfel/gipfel.sock`（`-u`）。
# 全仓库检索确认**没有任何消费方**——nginx 走的是 `127.0.0.1:8000`
# （`deploy/nginx-gipfel.conf`），脚本/测试/文档里也没有引用这个 socket。
# 而 daphne 对 Unix socket **不校验对端 UID**，同机任意用户只要能进入运行目录就能绕过
# nginx 直连后端，并且因为来源被判为回环，还可自造 `X-Real-IP` 绕过按 IP 的登录限速。
# X-13 已把运行目录收紧到 0750 作为纵深防御，这里直接把不需要的 socket 去掉，
# 攻击面从"仅 gipfel 组可达"降为"不存在"。
ExecStart=__INSTALL_DIR__/backend/.venv/bin/daphne \\
    -b 127.0.0.1 \\
    -p 8000 \\
"""

OLD_AF = """# 注意：必须包含 AF_UNIX——ExecStart 同时监听 Unix socket（/run/gipfel/gipfel.sock），
# 若 RestrictAddressFamilies 不含 AF_UNIX，内核会以 EAFNOSUPPORT（Errno 97）拒绝
# 创建套接字，daphne 任一监听失败即整体退出，服务无限重启。
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
"""

NEW_AF = """# 注意：必须包含 AF_INET（`-b 127.0.0.1`）与 AF_INET6；若缺失，内核会以
# EAFNOSUPPORT（Errno 97）拒绝创建套接字，daphne 任一监听失败即整体退出、服务无限重启。
# 审计 X-23：ExecStart 已不再监听 Unix socket，但 AF_UNIX 仍然保留 —— 名称解析（nss）、
# 日志与 systemd 通知等仍可能在用，去掉它属于额外风险，与本缺陷无关。
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
"""

OLD_RD = """# 运行目录（.sock + .pid）与日志目录；systemd 在启动前自动创建
#
# 审计 X-13：改前 RuntimeDirectoryMode=0755 —— `/run/gipfel` 对**所有本地用户**可遍历，
# 而该目录里放着 daphne 的 Unix socket `gipfel.sock`（daphne 不校验对端 UID），
# 于是同机任意用户都能绕过 nginx 直接连后端。现在收紧为 0750（仅 gipfel 组可进入）。
# 同时补 UMask=0027，确保服务自身新建的文件也不对其他用户开放。
"""

NEW_RD = """# 运行目录（pid/运行时状态）与日志目录；systemd 在启动前自动创建
#
# 审计 X-13：改前 RuntimeDirectoryMode=0755 —— `/run/gipfel` 对**所有本地用户**可遍历，
# 而该目录里放着 daphne 的 Unix socket `gipfel.sock`（daphne 不校验对端 UID），
# 于是同机任意用户都能绕过 nginx 直接连后端。现在收紧为 0750（仅 gipfel 组可进入）。
# 同时补 UMask=0027，确保服务自身新建的文件也不对其他用户开放。
# 审计 X-23：socket 本身已在 ExecStart 里移除（无消费方），0750 保留为纵深防御。
"""


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    changed = 0
    for old, new in ((OLD, NEW), (OLD_AF, NEW_AF), (OLD_RD, NEW_RD)):
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if o not in text:
            if n in text:
                print("[skip] 已打过补丁")
                continue
            print(f"[fail] 未找到锚点: {old.splitlines()[0][:70]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    TARGET.write_bytes(text.encode("utf-8"))
    b = TARGET.read_bytes()
    print(f"[ok]   替换 {changed}/3 处；gipfel.sock 残留={b.count(b'gipfel.sock')}（只应出现在说明注释里）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
