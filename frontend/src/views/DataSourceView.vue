<template>
  <div class="datasource-view">
    <div class="page-header">
      <h2>数据源管理</h2>
      <div>
        <!-- DSO-02: 全量健康检查 (admin) -->
        <el-button :loading="bulkHealthLoading" @click="checkAllHealth">
          全量健康检查
        </el-button>
        <!-- DSO-04: 全量元数据刷新 (admin) -->
        <el-button :loading="refreshMetaLoading" @click="refreshAllMetadata">
          刷新元数据
        </el-button>
        <el-button type="primary" :icon="Plus" @click="showCreate = true">
          新建数据源
        </el-button>
      </div>
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
      <el-table-column label="扫描" width="200">
        <template #default="{ row }">
          <!-- 扫描中: 进度条 + 步骤文字 (对标 V1 + 经验教训 #25) -->
          <template v-if="row.scan_status === 'scanning'">
            <el-progress :percentage="row.scan_progress" :stroke-width="14" :text-inside="true" status="success" />
            <div class="scan-stage">{{ row.scan_stage }}</div>
          </template>
          <!-- 扫描完成 -->
          <el-tag v-else-if="row.scan_status === 'done'" type="success" size="small">已扫描</el-tag>
          <!-- 扫描失败: 红色 + hover 显示错误 -->
          <el-tooltip v-else-if="row.scan_status === 'failed'" :content="row.scan_error || '扫描失败'" placement="top">
            <el-tag type="danger" size="small">扫描失败</el-tag>
          </el-tooltip>
          <span v-else class="scan-idle">未扫描</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="420" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :loading="row.scan_status === 'scanning'" @click="scan(row)">
            扫描
          </el-button>
          <el-button size="small" @click="viewSemantic(row)">
            查看语义层
          </el-button>
          <!-- DSO-02: 单数据源健康检查 -->
          <el-button
            size="small"
            :loading="healthCheckingId === row.id"
            @click="checkHealth(row)"
          >健康检查</el-button>
          <!-- DSO-08: 启停数据源 (admin) -->
          <el-button
            size="small"
            :type="row.is_active ? 'warning' : 'success'"
            :loading="togglingId === row.id"
            plain
            @click="toggle(row)"
          >
            {{ row.is_active ? '禁用' : '启用' }}
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
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { datasource, type DataSource } from '@/api'
import { extractErrorDetail } from '@/utils/error'

const router = useRouter()
const list = ref<DataSource[]>([])
const loading = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const togglingId = ref<string | null>(null)
// DSO-02/04
const healthCheckingId = ref<string | null>(null)
const bulkHealthLoading = ref(false)
const refreshMetaLoading = ref(false)

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
    ElMessage.error('加载失败: ' + (extractErrorDetail(e)))
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
    ElMessage.error('创建失败: ' + (extractErrorDetail(e)))
  } finally {
    creating.value = false
  }
}

// 扫描轮询: 每个数据源一个定时器 (key=ds_id), 触发后轮询 get 拿进度
const scanTimers = new Map<string, ReturnType<typeof setInterval>>()

async function scan(row: DataSource) {
  if (row.scan_status === 'scanning') {
    ElMessage.warning('该数据源正在扫描中，请等待完成')
    return
  }
  try {
    await datasource.scan(row.id)
    ElMessage.info('扫描已启动（含 LLM 中文推断），进度会实时更新...')
    startPollingScan(row.id)
  } catch (e: any) {
    ElMessage.error('启动扫描失败: ' + (extractErrorDetail(e)))
  }
}

function startPollingScan(dsId: string) {
  // 已有定时器则不重复
  if (scanTimers.has(dsId)) return
  let _pollFailCount = 0
  const poll = async () => {
    try {
      const { data } = await datasource.get(dsId)
      _pollFailCount = 0  // 成功则重置失败计数
      // 更新列表中对应行 (响应式: 直接改对象属性触发刷新)
      const row = list.value.find(d => d.id === dsId)
      if (row) {
        row.scan_status = data.scan_status
        row.scan_progress = data.scan_progress
        row.scan_stage = data.scan_stage
        row.scan_error = data.scan_error
      }
      // 终态: 停止轮询
      if (data.scan_status === 'done') {
        stopPollingScan(dsId)
        ElMessage.success(`扫描完成: ${data.scan_stage || ''}`)
      } else if (data.scan_status === 'failed') {
        stopPollingScan(dsId)
        ElMessage.error('扫描失败: ' + (data.scan_error || '未知错误'))
      }
    } catch {
      // 网络错误计数: 连续失败超限则停止轮询, 避免后端永久不可用时无限重试
      _pollFailCount = (_pollFailCount || 0) + 1
      if (_pollFailCount >= 10) {
        stopPollingScan(dsId)
        ElMessage.error('网络连接异常, 轮询已停止')
      }
    }
  }
  poll() // 立即查一次
  scanTimers.set(dsId, setInterval(poll, 2000)) // 2s 轮询扫描进度
}

function stopPollingScan(dsId: string) {
  const t = scanTimers.get(dsId)
  if (t) {
    clearInterval(t)
    scanTimers.delete(dsId)
  }
}

function viewSemantic(row: DataSource) {
  router.push(`/semantic?data_source_id=${row.id}`)
}

// DSO-08: 启停数据源 (确认后调 toggle, 刷新列表)
async function toggle(row: DataSource) {
  const action = row.is_active ? '禁用' : '启用'
  try {
    await ElMessageBox.confirm(
      `确认${action}数据源「${row.name}」? ${row.is_active ? '禁用后将无法查询和扫描。' : ''}`,
      `${action}确认`, { type: 'warning' },
    )
  } catch { return }
  togglingId.value = row.id
  try {
    await datasource.toggle(row.id, !row.is_active)
    ElMessage.success(`已${action}`)
    await fetchList()
  } catch (e: any) {
    ElMessage.error(`${action}失败: ` + (extractErrorDetail(e)))
  } finally {
    togglingId.value = null
  }
}

// DSO-02: 单数据源健康检查
async function checkHealth(row: DataSource) {
  healthCheckingId.value = row.id
  try {
    const { data } = await datasource.health(row.id)
    if (data.ok) {
      ElMessage.success(`「${row.name}」健康 ✓ (${data.latency_ms}ms)`)
    } else {
      ElMessage.error(`「${row.name}」异常: ${data.error || '未知错误'}`)
    }
  } catch (e: any) {
    ElMessage.error('检查失败: ' + (extractErrorDetail(e)))
  } finally {
    healthCheckingId.value = null
  }
}

// DSO-02: 全量健康检查 (admin)
async function checkAllHealth() {
  bulkHealthLoading.value = true
  try {
    const { data } = await datasource.checkAllHealth()
    ElMessage.success(`检查完成: ${data.healthy} 健康 / ${data.unhealthy} 异常${data.recovered ? ` / ${data.recovered} 恢复` : ''}${data.newly_error ? ` / ${data.newly_error} 新异常` : ''}`)
    await fetchList()
  } catch (e: any) {
    ElMessage.error('全量检查失败: ' + (extractErrorDetail(e)))
  } finally {
    bulkHealthLoading.value = false
  }
}

// DSO-04: 全量元数据刷新 (admin)
async function refreshAllMetadata() {
  try {
    await ElMessageBox.confirm('将检测所有数据源表结构变更, 有变更自动写新版本。继续?', '元数据刷新', { type: 'warning' })
  } catch { return }
  refreshMetaLoading.value = true
  try {
    const { data } = await datasource.refreshAllMetadata()
    ElMessage.success(`刷新完成: ${data.refreshed} 个有变更 / ${data.unchanged} 个无变化${data.failed ? ` / ${data.failed} 失败` : ''}`)
  } catch (e: any) {
    ElMessage.error('刷新失败: ' + (extractErrorDetail(e)))
  } finally {
    refreshMetaLoading.value = false
  }
}

onMounted(fetchList)

// 组件卸载时清理所有扫描轮询定时器 (防内存泄漏)
onUnmounted(() => {
  scanTimers.forEach(t => clearInterval(t))
  scanTimers.clear()
})
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
.scan-stage {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.scan-idle {
  font-size: 12px;
  color: #c0c4cc;
}
</style>
