#!/usr/bin/env bash
# ============================================================
# 复现/回归：gipfel 运行用户与组的创建路径
#
# 用途：验证「系统里没有名为 gipfel 的用户和组时，部署脚本是否报错」这一假设，
#       并固化 4 个真实故障场景作为回归证据。
#
# 运行环境：Linux（真 root + 真 useradd + 真 systemd），例如 WSL：
#   wsl -u root -e bash -lc "sed -i 's/\r$//' /mnt/c/.../code_audit/_repro_gipfel_user_group.sh; \
#                            bash /mnt/c/.../code_audit/_repro_gipfel_user_group.sh"
#
# 副作用：临时创建/删除 gipfel、gtmp 用户与组，写 /opt/gipfel-repro* 与一个临时 systemd unit，
#         每个场景结束（含退出）都会清理，不触碰 /opt/gipfel 真实部署。
#
# 结论（2026-09 实测，Ubuntu 26.04 / shadow-utils / systemd 255+）：
#   A 全新机器（无用户、无组、安装目录不存在）→ deploy-linux.sh 的建用户行 rc=0，chown OK，
#     **不会报错**：脚本的 useradd -U 会同时建用户和组。
#   B 组存在、用户不存在 → useradd -U rc=9（"group gipfel exists"），被 || true 吞掉，
#     随后的 chown -R gipfel:gipfel 报 `chown: invalid user: 'gipfel:gipfel'`；
#     在 set -euo pipefail 下脚本于 deploy-linux.sh:639 / update-from-github.sh:427 中止。
#   C 组里有其他成员时 userdel gipfel，组会保留（"group gipfel not removed because it has
#     other members"）→ 这是进入场景 B 的真实运维路径。
#   D unit 里 User=gipfel 而用户不存在 → status=217/USER；
#     Group=gipfel 而组不存在 → status=216/GROUP。
#   E 不带 -U 的 useradd（migrate-server.sh:339/501、docs/MIGRATION.md:242）在
#     USERGROUPS_ENAB=no 时只建用户不建组 → chown 报 `invalid group`。
# ============================================================
set -u

DIR=/opt/gipfel-repro
PASS=0
FAIL=0

cleanup() {
    userdel gtmp   >/dev/null 2>&1 || true
    userdel gipfel >/dev/null 2>&1 || true
    groupdel gipfel >/dev/null 2>&1 || true
    rm -rf "$DIR" /opt/gipfel-repro4 /opt/gipfel-repro5
    rm -f /etc/systemd/system/gipfel-repro.service /etc/systemd/system/gipfel-repro2.service
    systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup EXIT

expect() { # expect <描述> <期望值> <实际值>
    if [[ "$2" == "$3" ]]; then echo "  PASS: $1（$3）"; PASS=$((PASS + 1));
    else echo "  FAIL: $1（期望 $2，实际 $3）"; FAIL=$((FAIL + 1)); fi
}

[[ $EUID -eq 0 ]] || { echo "请用 root 运行"; exit 2; }

echo "环境：$(grep PRETTY_NAME /etc/os-release | cut -d= -f2- | tr -d '\"') / useradd=$(command -v useradd)"

echo
echo "=== A. 全新机器：无 gipfel 用户、无组、安装目录不存在 ==="
cleanup
expect "A 前置：无用户" "no such user" "$(id gipfel 2>&1 | sed 's/.*: //')"
expect "A 前置：无组" "" "$(getent group gipfel || true)"
useradd -r -s /usr/sbin/nologin -U -d "$DIR" gipfel; rc=$?
expect "A deploy-linux.sh:298 useradd 退出码" "0" "$rc"
mkdir -p "$DIR"; touch "$DIR/app.py"
chown -R gipfel:gipfel "$DIR"; rc=$?
expect "A deploy-linux.sh:639 chown 退出码" "0" "$rc"
expect "A 目录属主" "gipfel:gipfel" "$(ls -ld "$DIR" | awk '{print $3":"$4}')"

echo
echo "=== B. 组存在、用户不存在（deploy-linux.sh 守卫只查用户，useradd 带 -U）==="
cleanup
groupadd gipfel
mkdir -p "$DIR"; touch "$DIR/app.py"
useradd -r -s /usr/sbin/nologin -U -d "$DIR" gipfel 2>/dev/null; rc=$?
expect "B useradd -U 退出码（组已存在）" "9" "$rc"
expect "B 用户仍不存在" "no such user" "$(id gipfel 2>&1 | sed 's/.*: //')"
chown -R gipfel:gipfel "$DIR" 2>/dev/null; rc=$?
expect "B chown 退出码" "1" "$rc"
(
  set -euo pipefail
  trap 'rc=$?; echo "  B 真实控制流：脚本终止（退出码 ${rc}）——对应 deploy-linux.sh:639"; exit $rc' ERR
  if ! id gipfel >/dev/null 2>&1; then useradd -r -s /usr/sbin/nologin -U -d "$DIR" gipfel || true; fi
  chown -R gipfel:gipfel "$DIR"
) >/dev/null 2>&1; rc=$?
expect "B set -e 下脚本退出码" "1" "$rc"

echo
echo "=== C. 组里有其他成员时 userdel gipfel，组会留下（进入 B 的真实路径）==="
cleanup
useradd -r -s /usr/sbin/nologin -U gipfel
useradd -r -s /usr/sbin/nologin gtmp
usermod -aG gipfel gtmp
userdel gipfel 2>/dev/null
expect "C userdel 后用户消失" "no such user" "$(id gipfel 2>&1 | sed 's/.*: //')"
expect "C userdel 后组仍存在" "gipfel" "$(getent group gipfel | cut -d: -f1)"

echo
echo "=== D. systemd unit：User/Group 不存在 ==="
cleanup
cat > /etc/systemd/system/gipfel-repro.service <<'UNIT'
[Unit]
Description=gipfel user-missing repro
[Service]
Type=oneshot
User=gipfel
Group=gipfel
ExecStart=/bin/true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start gipfel-repro.service >/dev/null 2>&1 || true
D1="$(systemctl status gipfel-repro.service --no-pager 2>&1 || true)"
if grep -q '217/USER' <<<"$D1"; then echo "  PASS: D1 无用户 → 217/USER"; PASS=$((PASS + 1));
else echo "  FAIL: D1 无用户未报 217/USER"; FAIL=$((FAIL + 1)); fi
grep -m1 'Failed to determine credentials' <<<"$D1" | sed 's/^ *//;s/^/    /' || true

echo
echo "=== D2. systemd unit：Group=gipfel 不存在（用户存在）==="
cleanup
useradd -r -s /usr/sbin/nologin -N gipfel   # 只建用户不建组
cat > /etc/systemd/system/gipfel-repro2.service <<'UNIT'
[Unit]
Description=gipfel group-missing repro
[Service]
Type=oneshot
User=gipfel
Group=gipfel
ExecStart=/bin/true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start gipfel-repro2.service >/dev/null 2>&1 || true
D2="$(systemctl status gipfel-repro2.service --no-pager 2>&1 || true)"
if grep -q '216/GROUP' <<<"$D2"; then echo "  PASS: D2 无组 → 216/GROUP"; PASS=$((PASS + 1));
else echo "  FAIL: D2 无组未报 216/GROUP"; FAIL=$((FAIL + 1)); fi
grep -m1 'Failed to determine credentials' <<<"$D2" | sed 's/^ *//;s/^/    /' || true
cleanup

echo
echo "=== E. 不带 -U 的 useradd（migrate-server.sh / MIGRATION.md）在 USERGROUPS_ENAB=no 时 ==="
cleanup
useradd -r -K USERGROUPS_ENAB=no -s /usr/sbin/nologin gipfel; rc=$?
expect "E useradd 退出码" "0" "$rc"
expect "E 组未被创建" "" "$(getent group gipfel || true)"
mkdir -p "$DIR"
chown -R gipfel:gipfel "$DIR" 2>/dev/null; rc=$?
expect "E chown gipfel:gipfel 退出码" "1" "$rc"

echo
echo "=========================================="
echo "PASS=$PASS FAIL=$FAIL"
echo "=========================================="
cleanup
[[ $FAIL -eq 0 ]]
