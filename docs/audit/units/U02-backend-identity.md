# U02 backend identity/realtime（分支归属：master 基线）

## 概述

审计范围（严格限定，共 9 个模块、约 30 个源文件，全部逐行读过）：

| 模块 | 文件 |
| --- | --- |
| `backend/apps/auth/**` | `authentication.py`（JWT 编解码 + DRF 认证类）、`views.py`（登录/改密/资料/版本/两个网关令牌）、`bootstrap.py`（默认超管种子）、`urls.py`、`apps.py` |
| `backend/apps/users/**` | `models.py`、`serializers.py`、`views.py`、`urls.py`、`admin.py`、3 个迁移 |
| `backend/apps/audit/**` | `models.py`、`serializers.py`、`views.py`、`urls.py`、`admin.py` |
| `backend/apps/realtime/**` | `gateway.py`（Socket.IO 握手/订阅/重放）、`emit.py`（seq/环形缓冲/房间广播） |
| `backend/apps/files/**` | `views.py`（通用上传 + 地图背景） |
| `backend/apps/widget_packages/**` | `views.py`（zip 上传/解压/manifest）、`models.py` |
| `backend/apps/announcements/**` | `views.py`、`models.py`、`seed_announcements.py` |
| `backend/apps/messages/**` | `views.py`、`serializers.py`、`models.py`、`urls.py` |
| `backend/logviewer/**` | `logviewer/{settings,views,logutil,urls,asgi,wsgi}.py`、`manage.py`、`static/app.js`、`templates/index.html` |

分支归属核对：`git diff --stat master -- <上述全部路径>` 输出为空（`backend/apps/common` 同样为空），确认这些文件与 master 基线一致，本报告结论按 master 内容成立。

交叉阅读（用于判定可达性/后果，不计入本模块缺陷归属）：`backend/apps/common/{guards,permissions,helpers,middleware,audit,signals,pagination,exceptions}.py`、`backend/backend/{settings,urls,asgi}.py`、`backend/.venv` 内 Django 5.0.14 / DRF 3.15.1 源码（用于确认 `DATA_UPLOAD_MAX_MEMORY_SIZE` 不覆盖文件部分、`BooleanField.to_python` 的取值集合）、前端 `frontend/src/{realtime/socket.ts,realtime/resource-changed.ts,stores/auth.ts,stores/…}`（仅用于确认服务端行为在前端的真实后果，不作为缺陷归属）。

结论摘要：**P0 0 条；P1 2 条；P2 6 条；P3 11 条，共 19 条**。最高危方向是「**会话吊销不彻底**」：JWT 只在握手/请求瞬间校验 `token_version`，禁用账号（`is_active=false`）与已建立的 WebSocket 连接都不受吊销影响；其次是「**上传/解压无资源上限**」与「**登录限流可绕过**」。

已核对且**未发现问题**的点（写进结论以便上游去重，不再列为缺陷）：

- JWT 算法固定 `HS256` 且 `algorithms=[...]` 白名单（`backend/apps/auth/authentication.py:20,76`），`iss/aud/exp` 全部校验；`JWT_SECRET` 启动期 fail-fast + 弱密钥黑名单（`backend/backend/settings.py:23-43`），`backend/.env` 已被 `.gitignore` 忽略（`git ls-files` 仅含 `.env.example`），仓库内无密钥泄漏。
- token 里只有 `sub/username/role/tv/cid`，无密码/权限明细；授权判定的 `role`/`permissions` 一律现查 DB（`backend/apps/common/guards.py:82-83`），改角色后旧 token 不会保留旧权限。
- 强制改密（`must_change_password`）在认证层拦截除改密接口外的全部请求（`authentication.py:117-122`），非「只在前端做」。
- 审计 `changes` 对 `password_hash/token/secret` 等递归脱敏（`backend/apps/common/audit.py:18-44`）。
- 客户端 IP 只认可信代理（默认回环）注入的 `X-Real-IP`，**从不信任 `X-Forwarded-For`**（`backend/apps/common/helpers.py:107-136`），审计/限流的 IP 归属不可经 XFF 伪造。
- `parse_pagination` 对 0/负数/超大 `pageSize` 全部夹紧（上限 200，`pagination.py:30-38`）；`logviewer` 的 `lines=max(1,min(lines,5000))`。
- `files` 上传有 10MB 上限 + MIME 白名单 + 魔数校验 + 强制改名与扩展名（`files/views.py:179-207`）；`_delete_file_safe` 拒绝含 `..`/分隔符的文件名并做 realpath 前缀校验（`files/views.py:144-159`）。
- `widget_packages` 拒绝含 `..` 与绝对路径的 zip 条目（`widget_packages/views.py:89-91`）；`messages` 图片按魔数（而非 `content_type`）识别，落盘名用 `uuid4().hex`（`messages/views.py:48-63,291`）。
- `logviewer` 的 `_safe_path` 只允许 basename 且 realpath 的父目录必须等于 `LOG_ALLOW_DIR`（`logutil.py:34-45`），`..`、`.`、子目录、软链均被拒；`SECRET_KEY` 缺失直接拒绝启动（`logviewer/settings.py:29-35`）。
- Socket.IO：无 token 直接拒绝连接（`gateway.py:107-109`）；房间隔离 `user-{id}`/`comp-{id}` 在 `_can_join` 中按会话身份校验（`gateway.py:189-204`），`{room: "..."}` 形式的任意房间名会被 `_can_join` 拒绝；重放结果按房间二次过滤（`gateway.py:255-273`）。
- `messages` 详情越权（C2）已修：仅发布者本人或收件人可见（`messages/views.py:414-423`）；`audit`/`users` 只读或写接口均由 `account:manage` 守卫（按角色模板实际仅超管可持有）。
- 这些模块中**未发现仅在前端做权限校验**的接口：`widget_packages`、`announcements`、`messages`、`files` 的写操作都有服务端角色/权限判定。

---

## 缺陷清单

### [P1] I-01 顶号只发通知不断连：被踢设备的 WebSocket 仍留在 user-/comp- 房间持续收全部实时事件

- 位置：`backend/apps/realtime/gateway.py:119-127`（结合 `:142-155` 的连接期入房、`backend/apps/auth/views.py:156-162` 的顶号触发）
- 代码：
```python
    if user is None:
        # 顶号：老 token 被新版本号顶掉，把该用户的所有旧连接踢掉
        sub = payload.get("sub") if payload else None
        try:
            sub_int = int(sub)
            await sio.emit(
                "auth:required",
                {"reason": "token_version_mismatch"},
                room=f"user-{sub_int}",
            )
```
- 触发条件：设备 A 已建连（connect 时进入 `user-{id}`，并按 `competition_id` 自动进入 `comp-{cid}`，`gateway.py:152-155`）→ 设备 B 用同一账号登录，`LoginView` 递增 `token_version` 并广播 `auth:required`（`auth/views.py:156-162`）。服务端**只广播事件，从不 `sio.disconnect(sid)`，也没有任何周期性 tv 复核**：`_resolve_user`（`gateway.py:63-79`）只在 connect 握手时查一次 `payload.get("tv") != user.token_version`。只要 A 端不主动断开（攻击者用原生 socket.io 客户端，或改写前端不响应 `auth:required`），心跳（`ping_interval=25, ping_timeout=20`，`gateway.py:57-58`）会让连接一直存活。
- 后果：单点登录/顶号形同虚设。HTTP 侧 A 的旧 token 已 401，但实时通道继续把 `resource:changed`（该比赛全部业务变更的 resource/id/action）、`message:new`（**含消息标题、正文、图片 URL**，`messages/views.py:381-393`）推给 A。即「已经作废的会话仍能持续读取该用户与该比赛的实时数据」——被顶掉的设备恰是最可能已被他人控制的设备。
- 修复建议：登录/改密/禁用/删除用户时按 user_id 主动断开该用户既有 sid（维护 `user_id -> {sid}` 索引后逐个 `await sio.disconnect(sid)`，必要时先 `emit("auth:required")` 再断开）；或给每个 sid 记录 `tv`，在每次事件分发前复核，tv 不匹配即 leave_room + disconnect。

---

### [P1] I-02 禁用账号（isActive=false）不吊销已签发 token：JWT 与 WebSocket 均继续可用（最长 24h）

- 位置：`backend/apps/auth/authentication.py:104-114`（`_get_user` 见 `:139-150`；改 isActive 的路径 `backend/apps/users/serializers.py:133`）
- 代码：
```python
        user = self._get_user(payload)
        if user is None:
            raise exceptions.AuthenticationFailed(
                "登录已过期，请重新登录", code="invalid_user"
            )

        # 顶号下线：token 中 tv 与用户当前 token_version 不一致
        if payload.get("tv") != user.token_version:
```
- 触发条件：超管 `PATCH /api/users/<id> {"isActive": false}`（前端账户管理删除/停用走的就是这条路径；`UserSerializer.update` 只写 `is_active` 列，**不递增 `token_version`**），或 Django admin 直接改 `users.is_active`。此后该账号手里/浏览器里的旧 JWT 继续通过 `JWTAuthentication`：`_get_user` 仅 `User.objects.get(pk=sub)`，认证类里只有 `tv` 与 `must_change_password` 两道检查，**全程没有 `is_active` 判断**；`is_active` 唯一被检查的地方是新登录（`auth/views.py:153`）。WebSocket 侧 `_resolve_user`（`gateway.py:63-79`）同样不查 `is_active`。
- 后果：被禁用（离职、作弊、封禁）的账号在其 token 过期前（默认 `JWT_EXPIRES_IN=24h`）仍可读写本比赛全部业务数据；已建立的 socket 更是无限期有效。运维视角是「已经停用了账号，但对方仍在操作/看数据」，且无任何日志提示。
- 修复建议：`authenticate()` 中加 `if not getattr(user, "is_active", True): raise AuthenticationFailed(...)`；`UserUpdateView` 在 `isActive` 由 true→false 时递增 `token_version` 并 `emit_to_users([id], "auth:required")`；`gateway._resolve_user` 同步校验 `is_active`。

---

### [P2] I-03 管理员重置密码不吊销旧 token（与改密/登录的吊销逻辑不一致）

- 位置：`backend/apps/users/views.py:159-165`
- 代码：
```python
        user.set_password(password)
        if user.id == request.user.id:
            user.must_change_password = False
        else:
            must_change = request.data.get("mustChangePassword")
            user.must_change_password = True if must_change is None else bool(must_change)
        user.save(update_fields=["password_hash", "must_change_password", "updated_at"])
```
- 触发条件：账号疑似泄露 → 超管 `PATCH /api/users/<id>/password {"password": "..."}` 重置密码。该接口只写 `password_hash/must_change_password/updated_at`，**没有递增 `token_version`**；而同一仓里的改密接口（`auth/views.py:250-261`）和登录接口（`auth/views.py:157-158`）都显式递增了 `token_version`。
- 后果：重置密码这一最常用的应急处置无法把攻击者踢下线——已签发的 JWT（含攻击者复制的 token）继续有效到 `exp`（最长 24h），`isActive`/`mustChangePassword` 也不会影响旧 token（见 I-02）。响应里甚至返回 `UserSerializer(user).data`（`views.py:166`）给出「改密成功」的假象。
- 修复建议：重置密码时 `user.token_version = (user.token_version or 0) + 1` 并加入 `update_fields`，同时 `emit_to_users([user.id], "auth:required", {"reason": "password_reset"})` 断开其 socket。

---

### [P2] I-04 没有服务端登出/令牌吊销端点：登出仅是前端丢弃 token

- 位置：`backend/apps/auth/urls.py:16-22`
- 代码：
```python
urlpatterns = [
    path("login", LoginView.as_view(), name="auth-login"),
    path("me", MeView.as_view(), name="auth-me"),
    path("change-password", ChangePasswordView.as_view(), name="auth-change-password"),
    path("logviewer-token", LogViewerTokenView.as_view(), name="auth-logviewer-token"),
    path("backend-token", BackendTokenView.as_view(), name="auth-backend-token"),
]
```
- 触发条件：用户点击「退出登录」。前端 `logout()` 只清 `token`/`currentCompetition` 并 `disconnectRealtime()`（`frontend/src/stores/auth.ts:183-197`），**没有任何后端调用**；后端也没有 logout 路由、没有 `jti`/黑名单/`refresh token`，唯一能让旧 token 失效的手段是「同账号再登录一次」或「改密」（`authentication.py:110-114` 只比对 `tv`）。
- 后果：任何被复制走的 token（公用电脑、浏览器历史/扩展、XSS 窃取、日志或抓包泄漏）在 `exp` 前始终可用，用户与管理员都无法主动终止该会话；共享电脑上「退出登录」并不能阻止下一个人用抓到的 token 继续操作。
- 修复建议：新增 `POST /api/auth/logout`：递增 `token_version` + 断开该用户 socket（若要做「多设备互不影响」则改为 `jti` 黑名单或短 TTL + refresh token 轮换）。

---

### [P2] I-05 登录失败限流可被「用户名尾随空格」绕过；非字符串 username 直接 500

- 位置：`backend/apps/common/middleware.py:258-266`（登录侧规范化在 `backend/apps/auth/views.py:126`）
- 代码：
```python
            username = ""
            try:
                import json

                body = json.loads(request.body or b"{}")
                username = body.get("username", "")
            except Exception:  # noqa: BLE001
                pass
            if is_login_locked(ip, username):
```
- 触发条件：限流状态以 `_locks[(ip, username)]` 为键，而这里取的 `username` 是**请求体原值、未做 strip**；登录视图用的却是 `username = (request.data.get("username") or "").strip()`（`auth/views.py:126`），随后 `User.objects.filter(username=username)`。因此 `{"username": "admin ", "password": "x"}` 记入的键是 `("ip", "admin ")`，与 `"admin"` 的锁互不影响 —— 每次换一个空格组合（`" admin"`、`"admin  "`、`"admin\t"`）即可获得新的 10 次尝试额度，而认证对象始终是同一账号。附带：`{"username": {"a": 1}, "password": "x"}` 会让 `_locks.get((ip, {...}))` 抛 `TypeError: unhashable type`（`is_login_locked` 无异常保护，`middleware.py:245-249`），请求 500。
- 后果：`LoginRateLimitMiddleware` 声称的「10 次/5 分钟 → 锁 15 分钟」对爆破完全无效（只有 bcrypt cost=12 的算力成本），任意账号可被无限在线猜测；畸形 body 触发 500 属健壮性问题（并由 `exception_handler` 写入一条 error 审计）。
- 修复建议：限流键与认证使用同一规范化函数（`str(body.get("username") or "").strip().lower()`），并对非字符串/超长 username 直接 400；更稳妥是把 `record_login_failure/is_login_locked` 的调用收敛到 `LoginView` 内部（只有视图知道真正的认证对象）。

---

### [P2] I-06 地图背景上传无大小上限，且整文件 `f.read()` 进内存（单请求可 OOM/写满磁盘）

- 位置：`backend/apps/files/views.py:231-238`（对比 `:179-180` 的通用上传上限）
- 代码：
```python
    @require_permissions(_EDIT_PERM)
    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            raise BusinessError("未收到文件", code=400, status_code=400)
        mime = f.content_type or ""
        ext = _assert_image_mime(mime)
        buf = f.read()
```
- 触发条件：`POST /api/files/map-background`（multipart，单文件、`Content-Type: image/png`、文件头为 PNG 魔数即可过校验）上传一个几十 GB 的文件。同文件里的 `UploadView` 有 `if f.size > _MAX_UPLOAD_BYTES`（10MB，`:179-180`），本路径**完全没有 `f.size` 检查**；Django 的 `DATA_UPLOAD_MAX_MEMORY_SIZE` 只统计非文件字段（已核对 `.venv/Lib/site-packages/django/http/multipartparser.py:220-249`），超过 `FILE_UPLOAD_MAX_MEMORY_SIZE`（默认 2.5MB）的文件先落到临时目录，再由 `f.read()` 一次性读入内存。
- 后果：单个请求即可同时写满临时磁盘并把 daphne 进程的 RSS 撑爆（OOM 被杀 → 全站不可用），事后 MEDIA_ROOT 里还会留下巨型文件（`comp-<cid>-<ts>.png`）。触发者需持有 `data:map:edit`（按角色模板仅超管），但这条路径把「超管误操作/超管账号沦陷」直接放大为整站 DoS，且前端的地图背景选择器完全可以选中一个巨大的 TIFF 改名文件。
- 修复建议：与 `UploadView` 对齐先校验 `f.size`（如 ≤10MB），再用 `f.chunks()` 分块落盘并在写盘前用 `head = f.read(16)` 做魔数校验；同时对 `MEDIA_ROOT` 设置配额/定期清理旧背景图。

---

### [P2] I-07 控件包 zip 解压无体积/条目数上限（zip bomb 可写满磁盘，失败路径还残留文件)

- 位置：`backend/apps/widget_packages/views.py:86-95`
- 代码：
```python
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # 安全检查：不允许路径穿越
                for name in zf.namelist():
                    if ".." in name or name.startswith("/"):
                        raise BusinessError("zip 文件包含非法路径", code=400, status_code=400)
                zf.extractall(extract_full)
        except zipfile.BadZipFile:
            os.remove(zip_path)
            raise BusinessError("无法解析 zip 文件", code=400, status_code=400)
```
- 触发条件：上传一个 ≤5MB 的 zip（只校验了 `uploaded.name.endswith(".zip")` 与 `uploaded.size > 5MB`，`:66-69`）：① 单条高压缩比条目（deflate 极限约 1032:1 → 约 5GB 解压数据）；② 十几万个零字节条目（~5MB 的中央目录可容纳 10 万+ 条目）→ inode 耗尽。代码只校验条目**名字**，从不看 `ZipInfo.file_size`/`compress_size`，也没有条目数上限。
- 后果：`extractall` 把全部解压结果写进 `MEDIA_ROOT/widget-packages/<uid>/`，磁盘写满即整站写库失败（SQLite 无法写入）；另外 `BadZipFile` 分支只删 `.zip` 不删已解压目录（对比 `BusinessError` 分支的 `shutil.rmtree`，`:96-102`），残留目录只能人工清理。触发者需超管（上传仅超管），属「误传/账号沦陷放大器」。
- 修复建议：解压前遍历 `zf.infolist()`，累计 `file_size`（如 ≤50MB）、限制条目数（如 ≤1000）与压缩比（如 ≤100:1），超限即拒绝；用逐条 `zf.open()` + 目标路径 `os.path.realpath` 前缀校验替代 `extractall`；所有失败分支统一清理 `extract_full` + `zip_path`。

---

### [P2] I-08 日志查看器登录接口无限流无锁定，且与 /admin 共用同一超管凭据

- 位置：`backend/logviewer/logviewer/views.py:97-106`
- 代码：
```python
@require_POST
def login_view(request):
    """校验 Django 后台超级管理员（auth_user.is_superuser）账号密码。"""
    data = _json_body(request)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return JsonResponse({"ok": False, "message": "用户名和密码不能为空"}, status=400)

    user = authenticate(request, username=username, password=password)
```
- 触发条件：该端点直接注册在 `logviewer/urls.py:20`，**不在防直连网关保护范围内**（网关只作用于 `index` 视图，`views.py:61-79`），任何人可无限次 POST；此处的认证对象是 `auth_user`（`logviewer/settings.py:111-118` 复用主库），失败时既无失败计数、无锁定、也无 IP 限速——主后端同类接口有 `LoginRateLimitMiddleware`（`common/middleware.py:252-273`），两套服务的安全基线不一致。
- 后果：可对 `/admin` 超管口令做无限在线爆破；一旦命中即可读取全量服务日志（用户名、IP、设备、异常堆栈、内部标识），并作为后续社工/横向的跳板。仓库文档把默认口令记为 `admin23`（`README.md:54`、`docs/OPS.md:147`、`scripts/bootstrap-dev.bat:196`），若部署沿用了该口令，此接口等于给了无门槛的超级管理员入口（`bootstrap.py:31-69` 同时把该口令写进业务超管与 Django 超管）。
- 修复建议：logviewer 登录复用主后端的失败计数/锁定实现（同 IP+用户名 10 次/5 分钟锁 15 分钟）并记录失败审计；或直接把该接口放到网关令牌之后（与 `index` 同一道门）；同时强制首次登录改密、并在部署脚本里禁止 `admin23`。

---

### [P3] I-09 并发登录 `token_version` 读改写竞态：两个 token 同时有效，顶号失效

- 位置：`backend/apps/auth/views.py:156-162`
- 代码：
```python
        # 顶号下线：递增 token_version，旧 token 立即失效
        user.token_version = (user.token_version or 0) + 1
        user.save(update_fields=["token_version", "updated_at"])

        # 立即通过 WebSocket 踢掉旧设备（不等新设备建立 socket 连接）
        from apps.realtime.emit import emit_to_users
        emit_to_users([user.id], "auth:required", {"reason": "token_version_mismatch"})
```
- 触发条件：同一账号几乎同时发出两个 `POST /api/auth/login`（两台设备双击登录、脚本并发、前端重试）。两个请求各自 `SELECT` 读到 `token_version=N`（autocommit，读与写是两条语句，非原子 `UPDATE ... SET v=v+1`），随后都写入 `N+1`；两次 `create_jwt(user)` 签出的 `tv` 都是 `N+1`。
- 后果：单点登录承诺被打破——两台设备同时保持有效登录，且后续任何一次新登录会一起踢掉它们；若攻击者已知口令，可借并发窗口让自己与受害者的会话并存（受害者下一次登录才会失效攻击者）。
- 修复建议：用数据库原子自增取号：`User.objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)` 后 `refresh_from_db()`，用返回的真实值签发；或对登录加 `select_for_update()` 事务。

---

### [P3] I-10 实时 seq 分配与环形缓冲写入不在同一临界区：重放窗口可能重复/漏发

- 位置：`backend/apps/realtime/emit.py:66-70`（与之分离的写入在 `:78-88`；二分消费在 `:91-107`）
- 代码：
```python
def _next_seq() -> int:
    global _seq_value
    with _seq_lock:
        _seq_value += 1
        return _seq_value
```
- 触发条件：两个线程同时广播（daphne 线程池并发处理 HTTP，`_after_commit` 回调也在各自线程执行）。线程 A 在 `_next_seq()` 拿到 `seq=41` 并释放 `_seq_lock`，线程 B 拿到 `42`；若 B 先取得 `_ring_lock` 执行 `_event_ring.append(...)`/`_ring_seqs.append(...)`（`emit.py:86-88`），环形缓冲与 `_ring_seqs` 的顺序就变成 `[..., 42, 41]`，**不再单调递增**。
- 后果：`replay_since` 用 `_bisect_right_seqs`（二分，假定单调）定位「首个 > lastSeq 的条目」（`emit.py:91-107`），顺序错乱会让算子落点偏移 → 客户端重连补发时重复应用某些事件、或跳过一批事件；表现为断线后界面数据长期不刷新（需手动刷新），且概率随并发广播量上升。
- 修复建议：把取号与 append 放进同一个 `_ring_lock` 临界区（在锁内 `_seq_value += 1` 并 append），或让 `_push_ring` 拒绝/修正非单调入口（断言 `seq > _ring_seqs[-1]`）。

---

### [P3] I-11 Socket 会话的角色/比赛快照不随账号变更刷新：降权后仍可订阅任意比赛房间

- 位置：`backend/apps/realtime/gateway.py:197-204`（快照写入在 `:142-150`）
- 代码：
```python
    if room.startswith("comp-"):
        try:
            cid = int(room[len("comp-"):])
        except ValueError:
            return False
        if session.get("role") == "SUPER_ADMIN":
            return True
        return cid == session.get("competition_id")
```
- 触发条件：超管连上 socket（会话内固化 `role="SUPER_ADMIN"`、`competition_id=None`），随后被 `PATCH /api/users/<id>` 降级为 `COMPETITION_ADMIN` 或改到别的比赛。其**已建立的连接**仍用旧快照判定 `_can_join`，因此 `subscribe {"competitionId": N}` 对任意 N 都返回 `{"ok": true}` 并加入 `comp-N`。
- 后果：调岗/降权不能立即切断实时订阅：被降权的账号仍持续收到原可访问比赛的 `resource:changed`（resource/id/action 元信息，足以推断其他比赛的业务节奏并触发其前端重拉），直到该用户重连（换 tab/断网/退出）。权限回收存在不确定延迟，与 REST 层「改权立即生效」不一致。
- 修复建议：`on_subscribe`/`on_sync_replay` 中按 `session["user_id"]` 现查 DB 的 role/competition_id（或校验 `permission_version`/`token_version` 是否与连接时一致），不一致即要求重连；在用户/角色/比赛变更接口里主动断开该用户的连接。

---

### [P3] I-12 审计与日志的操作者归属取自 Authorization 头的 token，且不复核 token_version

- 位置：`backend/apps/common/middleware.py:180-188`（消费方 `backend/apps/common/audit.py:57-75`；`apps/auth/views.py:140-143` 的注释已承认该现象）
- 代码：
```python
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return
        token = auth[7:]
        try:
            from apps.auth.authentication import decode_jwt_payload

            payload = decode_jwt_payload(token)
            if payload:
```
- 触发条件：`decode_jwt_payload` 只校验签名/`exp`/`iss`/`aud`，**不校验 `tv`、`is_active`、也不确认请求者身份**；而 `/api/auth/login` 是 `AllowAny`（`auth/views.py:123`）。用一个「已被顶号但未过期」的旧 token（例如从受害者旧设备/历史记录里拿到的）发 `POST /api/auth/login`（用户名密码随便填）：失败时该请求的日志行 operator 前缀记成旧 token 的主人；成功时 `LoginView` 的 `user.save()` 触发审计信号，`AuditLog.operator_id/operator_name` 同样记成旧 token 的主人（而 `record_id`/`changes` 是真正登录的账号）。
- 后果：审计日志与运行日志的操作者归属可被「已作废但未过期」的 token 冒名（可把连续的失败登录、账号变更记到某个受害者名下），事后追责/取证失真；本仓自己的注释也承认「日志行的 [xxx] 前缀取自 Authorization 头里（可能已失效的）token」。IP 归属不可伪造（见概述），但这一条使「谁干的」同样不可信。
- 修复建议：审计 operator 一律取 DRF 认证后的 `request.user`（把这部分从中间件移到认证后，如 DRF 的 `perform_authentication` 之后），或至少让中间件复用 `JWTAuthentication` 的完整校验（含 `tv`/`is_active`）；`AllowAny` 端点不推导 operator。

---

### [P3] I-13 角色扩展权限（grantExtras）永远无法授予，文档承诺的「超管显式放开」不存在

- 位置：`backend/apps/users/views.py:35-42`（判定实现在 `backend/apps/common/permissions.py:388-401`）
- 代码：
```python
def _assert_grant(request, target_role, permissions) -> None:
    """授予上限校验：操作者必须为超管，且授予的权限不越界。"""
    perms = permissions if permissions is not None else []
    allowed, violations = assert_grant_allowed(
        getattr(request.user, "role", None), target_role, perms
    )
    if not allowed:
        raise BusinessError("；".join(violations), code=400, status_code=400)
```
- 触发条件：超管给 `COMPETITION_ADMIN` 授予 `_COMPETITION_ADMIN_EXTRAS` 中的任意一项（`message:manage`、`contractType:manage`、`industryType:manage`、`company:manage`、`data:region:edit`）——`POST /api/users/<id>/permissions` 或 `PATCH /api/users/<id>{"permissions":[...]}`。判定逻辑是 `if perm not in ceiling: if perm in extras: violations.append(f"{perm} 在扩展集中，需超管显式放开")`：**extras 恒进 violations 分支**，全仓不存在任何「显式放开」的入口（`grep grantExtras` 只命中该定义与两处文档），前端账户管理页也没有对应开关。
- 后果：功能层面永远 400（提示「需超管显式放开」但无放开手段），`COMPETITION_ADMIN` 拿不到消息发布、公司/合同类型/产业类型管理、区域编辑等能力；`docs/合同可视化新建操作指南.md:117` 的「超管在 grantExtras 中放开」与实际不符，运维会据此误判为 bug 或直接改库（改库又会绕过 I-13 之外的整套校验）。
- 修复建议：把 extras 计入可授予上限（`ceiling | set(extras)`），或在权限目录里为 extras 增加真实的开关位（如角色级白名单表）并同步文档；同时在 `ROLE_TEMPLATES` 上补一条断言测试。

---

### [P3] I-14 两条改权路径不一致：`PATCH /api/users/<id>` 改权限不递增 permission_version、不推送 permissions:changed

- 位置：`backend/apps/users/views.py:114-124`（对照专用端点 `:175-192`）
- 代码：
```python
    def patch(self, request, pk):
        user = _get_user(pk)
        serializer = UserSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # 授予上限校验：以新角色为准，权限取「提交值或既有值」
        effective_role = serializer.validated_data.get("role", user.role)
        effective_perms = serializer.validated_data.get(
            "permissions", user.permissions_list
        )
        _assert_grant(request, effective_role, effective_perms)
        serializer.save()
```
（`UserSerializer.update` 会经 `_apply_json_fields` 写入 `permissions/company_scopes/view_company_scopes/...`；只有 `UserPermissionsView` 才会 `permission_version += 1` 并 `emit_permissions_changed`，`views.py:182-189`）
- 触发条件：超管在账户管理页编辑账号（前端 `AccountManagementView.vue:459-462` 走的就是 `usersApi.update`，payload 里带 `permissions`）或直接调 `PATCH /api/users/<id>` 改 `permissions`/`role`。
- 后果：被改账号的前端不会收到 `permissions:changed`，`permission_version` 也不变，`can()`/菜单/按钮继续按旧权限渲染（刚被降权的人界面仍显示可操作按钮，点了才 403；刚被提权的人看不到新按钮需重新登录）。后端每次请求仍按最新 DB 权限判定，因此**不是越权**，但「实时权限刷新」这一设计承诺在同一模块内被绕过，运维会看到「改了权限没生效」的假象。
- 修复建议：`UserUpdateView.patch` 中比较 `validated_data` 是否含 `permissions/role/companyScopes/...`，命中则一并递增 `permission_version` 并 `emit_permissions_changed`；或干脆让账户编辑复用 `UserPermissionsView`。

---

### [P3] I-15 `messages/selectable-users` 只需 message:view：任意选手可枚举本比赛全部账号

- 位置：`backend/apps/messages/views.py:196-202`（范围解析 `:94-109`）
- 代码：
```python
    @require_permissions("message:view")
    def get(self, request):
        actor = request.user
        cid, err = _parse_query_competition_id(request.query_params)
        if err is not None:
            raise err
        user_ids = _selectable_user_ids(actor, cid)
```
- 触发条件：任意 `PLAYER` 账号（角色模板默认权限含 `message:view`，`common/permissions.py:298-316,357-365`）`GET /api/messages/selectable-users`（可带 `competitionId`）。返回体包含本比赛全部账号的 `id/username/displayName/role/competitionId`（`:205-221`）。
- 后果：该接口的产品语义是「发布消息时的收件人选择器」（需要 `message:manage` 才有意义），却只要求只读的 `message:view`，导致普通选手可拿到管理员/其他选手的用户名与显示名，为口令猜测与社工提供目标清单；比赛场景下账号名单通常不应暴露给选手。
- 修复建议：改为 `@require_permissions("message:manage")`；若确需给选手使用，则只返回其公司/自身范围内的最小字段（id + displayName）。

---

### [P3] I-16 公告 `isActive` 无类型校验：非布尔值 500；字符串 `"false"` 反而存成启用

- 位置：`backend/apps/announcements/views.py:61-67`（PATCH 同类问题在 `:101-102`）
- 代码：
```python
        a = Announcement.objects.create(
            version=version,
            title=title,
            date=date,
            content=content,
            is_active=data.get("isActive", True),
        )
```
- 触发条件：① `POST /api/announcements {"version":"v","title":"t","content":"c","isActive":null}`（或 `""`、`"no"`）：值直接交给 `BooleanField`，`get_prep_value → to_python` 抛 `django.core.exceptions.ValidationError`，DRF 的 `exception_handler` 不接该类异常 → 走 `response is None` 分支返回 500（`common/exceptions.py:52-55`），而非 400。② `PATCH /api/announcements/<id> {"isActive":"false"}`：`a.is_active = bool("false")` → `True`（`views.py:102`）→ 「停用公告」静默变成启用（公告继续对所有登录用户展示）。同型的 `bool(...)` 反模式还在 `widget_packages/views.py:191`。
- 后果：脚本/非 JSON 客户端得到 500「服务器内部错误」（并留下一条 error 审计），真实原因被掩盖；把公告停用写成字符串会得到完全相反的效果，且响应里 `isActive: true` 会让操作者以为生效（实际也确实生效成了 true，与意图相反）。
- 修复建议：`POST/PATCH` 统一走 DRF `BooleanField` 校验（`str(v).strip().lower() in ("1","true","yes","on")`），非法类型返回 400；`widget_packages` 的 `isActive` 同样处理。

---

### [P3] I-17 消息 `images.filename` 完全由发布者提供：删除自己的消息会删掉他人上传的图片文件

- 位置：`backend/apps/messages/views.py:119-125`（发布入口 `:354-371`、删除入口 `:444`；校验在 `serializers.py:106-120`）
- 代码：
```python
        filename = it.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        try:
            p = os.path.join(upload_dir, os.path.basename(filename))
            if os.path.exists(p):
                os.remove(p)
```
- 触发条件：持有 `message:manage` 的账号发布消息时把 `images` 写成 `[{"url": "/uploads/message-images/xxx.png", "filename": "<他人上传的文件名>"}]` —— `MessageSerializer.validate_images` 只校验 `url`/`filename` 是字符串（`serializers.py:106-120`），不校验该文件是否属于本次会话；随后 `DELETE /api/messages/<id>` 时 `_delete_message_images` 按 `filename` 直接删盘。文件名是 `uuid4().hex + ext`（`views.py:291`），可从任何返回的图片 URL（收件箱/详情/公告）直接读出来。
- 后果：删除自己的一条消息即可删掉任意其他消息引用的图片文件，收件箱里那些消息的图片集体 404（跨对象破坏数据完整性，且无审计线索——文件删除不走 ORM，不产生审计行）。触发者需 `message:manage`（按角色模板实际仅超管），属「超管账号沦陷/内部误用」场景下的破坏面放大。
- 修复建议：上传接口返回一次性令牌/登记表（记录 uploader_id 与是否已被引用），发布时只接受「本人上传且未被引用」的文件；删除时再校验 `filename` 在登记表中确实归属该消息。

---

### [P3] I-18 顶号事件可被「已作废 token」反复触发：旧 token 持有者可反复把受害者踢下线

- 位置：`backend/apps/realtime/gateway.py:113-121`（广播体在 `:122-128`）
- 代码：
```python
    payload = decode_jwt_payload(token)
    if payload is None:
        logger.debug("socket.io 连接拒绝：JWT 无效 (sid=%s)", sid)
        return False

    user = await _resolve_user(payload)
    if user is None:
        # 顶号：老 token 被新版本号顶掉，把该用户的所有旧连接踢掉
        sub = payload.get("sub") if payload else None
```
- 触发条件：connect 时只要 `decode_jwt_payload` 通过（签名有效、未过期）而 `_resolve_user` 因 `tv` 不匹配返回 `None`，服务端就无条件向 `user-{sub}` 房间广播 `auth:required`——**这一路径对任何持有该用户旧 token 的人开放**（旧 token 会长期留在旧设备 localStorage/日志/抓包里，且顶号后并不主动断开，见 I-01）。攻击者反复发起 connect（每次都被拒）即可反复广播；受害者前端收到后立即 `window.dispatchEvent("auth:kicked")` → `logout()` 清 token 跳登录页（`frontend/src/realtime/socket.ts:75-79`、`stores/auth.ts:183-202`）。
- 后果：无需任何有效会话即可对「token 曾被泄露/曾被顶号」的账号做持续登出骚扰（可用性攻击）；同时它把「顶号」这一应由真实登录触发的语义暴露给了不可信输入（服务端无法区分「旧设备重连」与「攻击者重放」）。
- 修复建议：仅在确认存在该用户的既有 sid 时定向断开（遍历该用户的 sid 列表逐个 `disconnect`），而不是对房间广播通用事件；并且把 `auth:required` 的广播收敛到 `LoginView`/改密/禁用等可信路径。

---

### [P3] I-19 `permissions` 为 NULL 的账号不按角色继承权限，而是「零权限」

- 位置：`backend/apps/users/models.py:47-48,100-110`（判定在 `backend/apps/common/permissions.py:264-291`）
- 代码：
```python
    # 细粒度权限 JSON 数组（null 表示按 role 继承）
    permissions = models.TextField(null=True, blank=True)
```
```python
        if not self.permissions:
            return []
```
- 触发条件：绕过前端账户管理页创建账号时省略 `permissions`——例如 `POST /api/users {"username":"p1","password":"12345678"}`（`UserSerializer` 的 `permissions` 是 `required=False`，`create()` 里 `_apply_json_fields` 只在字段出现时写库，`serializers.py:153-156`），或在 Django admin（`users/admin.py:10` 用默认 ModelAdmin 注册）里直接建 `users` 行 → 库中 `permissions` 为 NULL。此后 `has_permission(role, [], "message:view")` 逐项匹配用户持有的权限，空列表恒不满足，**只有 SUPER_ADMIN 靠隐式旁路放行**（`common/permissions.py:264-265`）；`ROLE_TEMPLATES[...]["defaultPermissions"]` 只在 `assert_grant_allowed` 里被使用，从不参与运行时判定。
- 后果：与模型注释/产品语义「null 表示按 role 继承」不符：新建（脚本/后台/导入）账号登录成功后满屏无权限（连 `message:view`、`data:*:view` 都没有），所有业务接口 403，排查成本高；也让「权限为空=继承角色」成为不可实现的契约（同一份代码中的注释即错误文档）。
- 修复建议：让 `permissions_list` 在 `self.permissions is None` 时回退到 `ROLE_TEMPLATES[self.role]["defaultPermissions"]`，或在创建/导入路径把角色默认权限物化落库，并修正注释。

---

## 存疑/待确认

1. **[待确认] 非超管持有 `account:manage` 的后果**：`UserDeleteView`（`backend/apps/users/views.py:134-139`）与 `UserPasswordView`（`:152-166`）只校验 `account:manage`，**没有 `_assert_grant`、也不限制「目标是否超管」**——若某 `COMPETITION_ADMIN` 通过历史脏数据、Django admin 直接改 `users.permissions`，或归档导入（`backend/apps/preparation/archive.py:2462` 把归档文件里的 `permissions` 原样落库、不做授予上限校验）拿到 `account:manage`，就可在任意比赛内删除账号（含超管）或重置超管密码后登录，构成完整提权链。当前所有正常授权路径都被 `assert_grant_allowed` 拦住、归档导入需 `competition:manage`（仅超管），因此需先存在历史脏数据才成立；建议无论是否存在脏数据，都在密码重置/删除接口补一条「非超管不得操作 SUPER_ADMIN 账号」，并在运行时对 `SUPER_ADMIN_ONLY_PERMISSIONS` 做一次强制剔除。
2. **[待确认] Socket.IO 在未配置 `CORS_ORIGIN` 时退化为 `*`**（`backend/apps/realtime/gateway.py:39-51`，生产仅打一条 warning）：由于握手必须携带 `auth.token`（自定义头而非 Cookie），本次未找到可直接利用的 CSWSH 路径（浏览器跨站脚本拿不到受害者 localStorage 里的 token），故未列为缺陷；但生产漏配 `CORS_ORIGIN` 会让任意站点能向服务端发起 WS 连接（可用于连接数耗尽或配合未来的 Cookie 鉴权变成真漏洞），属应修加固项。
3. **[待确认] 私有房间事件不进重放环**：`emit_resource_changed_to_users`（`emit.py:314-345`）与 `emit_permissions_changed` 的注释明确「不进入环形重放」，`message:new`（`messages/views.py:381-393`）也只在发送瞬间推给在线收件人。断线期间的消息弹窗与未读红点不会由 `sync:replay` 补发，前端只能靠 `reconcileAllIncremental()` 全量对账兜底（`frontend/src/realtime/resource-changed.ts:162-174`）。是否算缺陷取决于产品对「离线期间的消息到达提醒」的要求（本轮按「功能取舍」处理，未计为缺陷）。
4. **[待确认] 日志查看器防直连令牌只验签不验 payload/身份**：`logviewer/views.py:70-77` 对 `signer.unsign(token, max_age=...)` 的结果不做任何检查（签发侧 `auth/views.py:189` 写的是 `lv:{user.id}`，可用性/身份均未校验），拿到任一超管签发的令牌即可在 120s 内由任意人（含未登录者）打开 SPA 外壳；令牌还长期留在 URL 中（浏览器历史、代理访问日志；`index.html` 未设 Referrer-Policy）。因为进入页面后仍需 `/admin` 超管登录，影响有限，按加固项处理。`BackendGateMiddleware`（`common/backend_gate.py:64-73`）同样只验签不验 payload。
5. **[待确认] `bootstrap.py` 的兜底口令 `admin23`（`backend/apps/auth/bootstrap.py:27`）在当前 settings 下不可达**（`settings.py:156-164` 未配置 `SEED_ADMIN_PASSWORD` 时会生成 16 字节随机口令），但 README/OPS 文档、`scripts/bootstrap-dev.bat:196` 仍以 `admin23` 作为默认告知值，容易引导部署者把弱口令显式写回 `.env`（本机 `backend/.env:32` 也留着被注释的 `# SEED_ADMIN_PASSWORD="admin23"`）。属配置/文档风险，未计为代码缺陷。
6. **[待确认] `widget_packages` 的 `manifest.component` 可做路径探测**：`views.py:142-146` 把 `manifest["component"]`（默认 `component.js`）与解压目录直接拼接后 `os.path.exists` 校验，传 `../../../../etc/passwd` 之类只得到「存在/不存在」的布尔差异（不返回内容），且前端加载的 URL 固定为 `{MEDIA_URL}{extract_dir}/component.js`（`:43`），本轮未找到可利用面；另 `manifest.label/type/version` 均无长度与字符校验（`widget_type` 落库 `max_length=128`、SQLite 不强制），超大 label 会在 MySQL/PG 下 500，属健壮性隐患。
7. **[待确认] `messages` 非超管发布路径的收件人范围边界**：`_selectable_user_ids`（`messages/views.py:94-109`）对非超管执行 `qs.filter(competition_id=actor.competition_id)`；若账号 `competition_id` 为 NULL（模型允许），该过滤会命中「全部无归属比赛的系统账号」（含超管）。当前 `message:manage` 无法授予非超管（见 I-13），故只在历史数据中已存在此类账号时可达，未单独计为缺陷。
