<template>
  <div class="datasource-view">
    <div class="page-header">
      <h2>数据源管理</h2>
      <el-button type="primary" :icon="Plus" @click="showCreate = true">
        新建数据源
      </el-button>
    </div>

    <!-- 数据源列表 -->
    <el-table :data="list" v-loading="loading" border stripe>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag :type="row.db_type === 'postgresql' ? 'success' : 'warning'" size="small">
            {{ row.db_type }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="连接">
        <template #default="{ row }">
          {{ row.host }}:{{ row.port }}/{{ row.database }}
        </template>
      </el-table-column>
      <el-table-column prop="username" label="用户" width="100" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
            {{ row.is_active ? '启用' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :loading="scanningId === row.id" @click="scan(row)">
            扫描
          </el-button>
          <el-button size="small" @click="viewSemantic(row)">
            查看语义层
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && list.length === 0" description="还没有数据源，点击右上角新建" />

    <!-- 创建对话框 -->
    <el-dialog v-model="showCreate" title="新建数据源" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：电商业务库" />
        </el-form-item>
        <el-form-item label="数据库类型">
          <el-radio-group v-model="form.db_type">
            <el-radio value="postgresql">PostgreSQL</el-radio>
            <el-radio value="mysql">MySQL</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="主机" required>
          <el-input v-model="form.host" placeholder="192.168.99.22" />
        </el-form-item>
        <el-form-item label="端口" required>
          <el-input-number v-model="form.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="库名" required>
          <el-input v-model="form.database" placeholder="njmind" />
        </el-form-item>
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { datasource, type DataSource } from '@/api'

const router = useRouter()
const list = ref<DataSource[]>([])
const loading = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const scanningId = ref<string | null>(null)

const form = reactive({
  name: '',
  db_type: 'postgresql' as 'postgresql' | 'mysql',
  host: '',
  port: 5432,
  database: '',
  username: '',
  password: '',
})

async function fetchList() {
  loading.value = true
  try {
    const { data } = await datasource.list()
    list.value = data
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function create() {
  creating.value = true
  try {
    await datasource.create({ ...form })
    ElMessage.success('创建成功')
    showCreate.value = false
    // 重置表单
    Object.assign(form, { name: '', host: '', database: '', username: '', password: '' })
    await fetchList()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

async function scan(row: DataSource) {
  scanningId.value = row.id
  try {
    ElMessage.info('扫描中（含 LLM 中文推断，稍等）...')
    const { data } = await datasource.scan(row.id)
    ElMessage.success(`扫描完成: v${data.version}, ${data.table_count} 张表`)
  } catch (e: any) {
    ElMessage.error('扫描失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    scanningId.value = null
  }
}

function viewSemantic(row: DataSource) {
  router.push(`/semantic?data_source_id=${row.id}`)
}

onMounted(fetchList)
</script>

<style scoped>
.datasource-view {
  padding: 24px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
}
</style>
