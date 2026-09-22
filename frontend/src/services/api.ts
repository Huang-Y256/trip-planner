import axios from 'axios'
import type { TripFormData, TripPlanResponse, TripPlan } from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000, // 2分钟超时
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

// SSE 事件结构
export interface StreamEvent {
  node: string
  message?: string
  status?: string
  done: boolean
  data?: {
    trip_plan?: TripPlan
    status?: string
    plan_id?: string
  }
}

/**
 * 流式生成旅行计划（SSE）
 *
 * 通过 fetch + ReadableStream 消费后端 LangGraph 节点执行进度，
 * 每个 SSE 事件通过 onProgress 回调通知调用方。
 */
export async function generateTripPlanStream(
  formData: TripFormData,
  onProgress: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<TripPlanResponse> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

  const response = await fetch(`${baseUrl}/api/trip/plan/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData),
    signal
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`请求失败 (${response.status}): ${errorText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('浏览器不支持流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let finalPlan: TripPlan | null = null
  let finalMessage = ''
  let finalPlanId = ''

  while (true) {
    const { done: readerDone, value } = await reader.read()
    if (readerDone) break

    buffer += decoder.decode(value, { stream: true })

    // 解析 SSE 格式：以 "\n\n" 分隔的 "data: {...}" 行
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''

    for (const part of parts) {
      const lines = part.split('\n')
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event: StreamEvent = JSON.parse(line.slice(6))
          onProgress(event)

          if (event.done && event.data?.trip_plan) {
            finalPlan = event.data.trip_plan
            finalMessage = event.message || '旅行计划生成完成'
            finalPlanId = event.data.plan_id || ''
          }
          if (event.done && event.node === 'error') {
            throw new Error(event.message || '生成失败')
          }
        } catch (e) {
          if (e instanceof Error && e.message !== 'Unexpected end of JSON input') {
            throw e
          }
        }
      }
    }
  }

  if (!finalPlan) {
    throw new Error(finalMessage || '生成失败，请稍后重试')
  }

  return { success: true, message: finalMessage, plan_id: finalPlanId || undefined, data: finalPlan }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

/**
 * 根据 plan_id 获取已保存的旅行计划
 */
export async function getTripPlan(planId: string): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.get<TripPlanResponse>(`/api/trip/plan/${planId}`)
    return response.data
  } catch (error: any) {
    console.error('获取旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || '获取旅行计划失败')
  }
}

/**
 * 更新旅行计划（编辑模式保存）
 */
export async function updateTripPlan(planId: string, plan: TripPlan): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.put<TripPlanResponse>(`/api/trip/plan/${planId}`, plan)
    return response.data
  } catch (error: any) {
    console.error('更新旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || '更新旅行计划失败')
  }
}

export default apiClient
