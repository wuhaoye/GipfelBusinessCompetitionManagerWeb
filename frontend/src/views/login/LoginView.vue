<template>
  <div class="login-page">
    <div class="login-card">
      <img class="login-logo" src="@/assets/gipfel-logo.jpg" alt="Gipfel" />
      <h1 class="login-title">Gipfel商赛系统</h1>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="0"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            size="large"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-dialog
      v-model="showChangeDialog"
      title="请修改初始密码"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
      width="420px"
    >
      <p class="change-tip">检测到您仍在使用初始密码，请修改后再继续。</p>
      <el-form :model="changeForm" label-width="92px">
        <el-form-item label="旧密码">
          <el-input v-model="changeForm.oldPassword" type="password" show-password placeholder="请输入当前密码" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="changeForm.newPassword" type="password" show-password placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="changeForm.confirm" type="password" show-password placeholder="再次输入新密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="changeForm.changing" @click="submitChangePassword"
          >确认修改</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { User, Lock } from "@element-plus/icons-vue";
import { useAuthStore } from "@/stores/auth";
import { getErrorMessage } from "@/api";
import type { FormInstance } from "element-plus";

const router = useRouter();
const authStore = useAuthStore();
const formRef = ref<FormInstance>();
const loading = ref(false);
const showChangeDialog = ref(false);

const form = reactive({
  username: "",
  password: "",
});

const rules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  password: [{ required: true, message: "请输入密码", trigger: "blur" }],
};

async function handleLogin() {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    try {
      await authStore.login(form.username, form.password);
      // 命中强制改密策略（默认超管首次登录）：弹窗改密，不进入业务界面
      if (authStore.needsPasswordChange) {
        // 预填刚输入的初始密码：改密表单的「原密码」就是它，省一次手输也避免输错
        changeForm.oldPassword = form.password;
        showChangeDialog.value = true;
        return;
      }
      ElMessage.success("登录成功");
      router.push("/dashboard");
    } catch {
      // error handled by interceptor
    } finally {
      loading.value = false;
    }
  });
}

// 已在登录态但需改密（如刷新页面）时自动弹出对话框
onMounted(() => {
  if (authStore.isLoggedIn && authStore.needsPasswordChange) {
    showChangeDialog.value = true;
  }
});

const changeForm = reactive({
  oldPassword: "",
  newPassword: "",
  confirm: "",
  changing: false,
});

async function submitChangePassword() {
  const pwd = changeForm.newPassword;
  if (pwd.length < 8 || pwd.length > 64) {
    ElMessage.warning("新密码长度需为 8-64 位");
    return;
  }
  if (!(/[a-zA-Z]/.test(pwd) && /\d/.test(pwd))) {
    ElMessage.warning("新密码需同时包含字母和数字");
    return;
  }
  if (changeForm.newPassword !== changeForm.confirm) {
    ElMessage.warning("两次输入的新密码不一致");
    return;
  }
  changeForm.changing = true;
  try {
    // 第三个参数带上用户名：万一登录态已被清空（历史缺陷），store 能用它 + 原密码恢复会话
    await authStore.changePassword(changeForm.oldPassword, changeForm.newPassword, form.username);
    ElMessage.success("密码修改成功");
    showChangeDialog.value = false;
    router.push("/dashboard");
  } catch (e) {
    ElMessage.error(getErrorMessage(e));
  } finally {
    changeForm.changing = false;
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh; /* 回退 */
  height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef1fe 0%, #f6f7fb 55%, #e9f6fb 100%);
  position: relative;
  overflow-y: auto; /* 键盘弹起/小屏时允许滚动到登录卡片 */
  -webkit-overflow-scrolling: touch;
}
.login-page::before {
  content: "";
  position: absolute;
  width: 540px;
  height: 540px;
  top: -180px;
  left: -140px;
  background: radial-gradient(circle, rgba(99, 102, 241, 0.18), rgba(99, 102, 241, 0) 70%);
}
.login-page::after {
  content: "";
  position: absolute;
  width: 480px;
  height: 480px;
  bottom: -170px;
  right: -120px;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.16), rgba(6, 182, 212, 0) 70%);
}
.login-card {
  position: relative;
  z-index: 1;
  width: min(400px, calc(100vw - 32px));
  padding: 44px 40px 40px;
  background: #fff;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--color-border);
  animation: cardFloat 0.5s var(--ease-spring) both;
}
@media (max-width: 640px) {
  .login-card {
    padding: 32px 24px 28px;
    border-radius: var(--radius-md);
  }
}
@keyframes cardFloat {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
.login-logo {
  width: 60px;
  height: 60px;
  margin: 0 auto 14px;
  border-radius: 15px;
  object-fit: cover;
  object-position: top center;
  display: block;
  box-shadow: 0 8px 20px rgba(var(--color-primary-rgb), 0.32);
}
.login-title {
  font-size: 21px;
  font-weight: 700;
  background: var(--gradient-brand);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
  text-align: center;
  margin: 0 0 4px;
}
.login-btn {
  width: 100%;
}
</style>
