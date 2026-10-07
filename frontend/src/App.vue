<template>
  <div>
    <div class="status-bar">
      <!-- 顶细条数直接数栏内条数：与左右分栏同一个 /board 世界 -->
      <span>可借 {{ availableCount }}</span>
      <span>在借 {{ activeCount }}</span>
      <span>逾期 {{ overdueCount }}</span>
    </div>
    <nav class="topnav">
      <router-link to="/">看板</router-link>
      <router-link to="/list">上架</router-link>
      <router-link to="/loans">借还记录</router-link>
      <router-link to="/owners">物主</router-link>
      <router-link to="/settings">设置</router-link>
    </nav>
    <router-view @refresh="load" />
  </div>
</template>
<script setup>
import { ref, computed, onMounted, provide } from 'vue'
import { api } from './api'
const board = ref({ available: [], active: [], overdue: [] })
// 顶细条数 = 各栏条数，杜绝"数与栏不是同一世界"
const availableCount = computed(() => (board.value.available || []).length)
const activeCount = computed(() => (board.value.active || []).length)
const overdueCount = computed(() => (board.value.overdue || []).length)
async function load() {
  board.value = await api('/board')
}
provide('board', board)
provide('reloadBoard', load)
onMounted(load)
</script>
