<template>
  <div class="page-container">
    <div class="header">
      <h2>数据源管理</h2>
      <div style="display: flex; gap: 12px; align-items: center;">
        <el-button @click="router.push('/')">返回对话</el-button>
        <el-button type="primary" @click="showDialog = true">添加数据源</el-button>
      </div>
    </div>

    <el-table :data="datasourceStore.datasources" v-loading="datasourceStore.loading" empty-text="暂无数据源">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="type" label="类型" width="80" />
      <el-table-column prop="host" label="主机" />
      <el-table-column prop="port" label="端口" width="80" />
      <el-table-column prop="database_name" label="数据库" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'">
            {{ row.status === 'active' ? '活跃' : '异常' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button size="small" @click="testConnection(row.id)">测试连接</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Add dialog -->
    <el-dialog v-model="showDialog" title="添加数据源" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="我的MySQL数据库" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.type" style="width: 100%">
            <el-option label="MySQL" value="mysql" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机" required>
          <el-input v-model="form.host" placeholder="127.0.0.1" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="数据库名" required>
          <el-input v-model="form.database_name" placeholder="mydb" />
        </el-form-item>
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="datasourceStore.loading" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { ElMessage, ElMessageBox } from 'element-plus'

const router = useRouter()
const datasourceStore = useDatasourceStore()

const showDialog = ref(false)
const form = reactive({
  name: '',
  type: 'mysql',
  host: '',
  port: 3306,
  database_name: '',
  username: '',
  password: '',
})

async function testConnection(id: string) {
  const result = await datasourceStore.testConnection(id)
  if (result.success) {
    ElMessage.success('连接成功')
  } else {
    ElMessage.error(result.error || '连接失败')
  }
}

async function handleDelete(id: string) {
  try {
    await ElMessageBox.confirm('确定要删除此数据源吗？', '确认删除', { type: 'warning' })
  } catch {
    return
  }
  const error = await datasourceStore.remove(id)
  if (error) {
    ElMessage.error(error)
  } else {
    ElMessage.success('已删除')
  }
}

async function handleCreate() {
  if (!form.name || !form.host || !form.database_name || !form.username || !form.password) {
    ElMessage.warning('请填写必填项')
    return
  }
  const result = await datasourceStore.create(form)
  if (result.success) {
    ElMessage.success('创建成功')
    showDialog.value = false
    Object.assign(form, { name: '', host: '', database_name: '', username: '', password: '' })
    await datasourceStore.list()
  } else {
    ElMessage.error(result.error)
  }
}

onMounted(() => {
  datasourceStore.list()
})
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.header h2 {
  margin: 0;
}
</style>
