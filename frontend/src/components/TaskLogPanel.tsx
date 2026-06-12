import { useEffect, useRef, useState } from 'react'
import { Button, Input, message, Modal, Space } from 'antd'
import { CopyOutlined, FastForwardOutlined, StopOutlined } from '@ant-design/icons'

import { API_BASE, apiFetch, getToken } from '@/lib/utils'

interface TaskLogPanelProps {
  taskId: string
  onDone?: () => void
}

type TaskTerminalStatus = 'idle' | 'done' | 'failed' | 'stopped'

export function TaskLogPanel({ taskId, onDone }: TaskLogPanelProps) {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState('')
  const [terminalStatus, setTerminalStatus] = useState<TaskTerminalStatus>('idle')
  const [skipLoading, setSkipLoading] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [stopRequested, setStopRequested] = useState(false)
  // OTP 弹窗状态
  const [otpSlot, setOtpSlot] = useState<string | null>(null)
  const [otpHint, setOtpHint] = useState('')
  const [otpCode, setOtpCode] = useState('')
  const [otpSubmitting, setOtpSubmitting] = useState(false)
  const pendingSlotsRef = useRef<Set<string>>(new Set())

  const panelRef = useRef<HTMLDivElement>(null)
  const onDoneRef = useRef(onDone)
  const nextSinceRef = useRef(0)

  const isFinished = terminalStatus !== 'idle' || stopRequested

  // 检测新日志行中的 OTP_REQUIRED 标记
  const checkOtpRequired = (line: string) => {
    const match = line.match(/\[OTP_REQUIRED:([a-f0-9]+)\](.*)/)
    if (match) {
      const slot = match[1]
      if (!pendingSlotsRef.current.has(slot)) {
        pendingSlotsRef.current.add(slot)
        setOtpSlot(slot)
        setOtpCode('')
        setOtpHint(match[2]?.trim() || '请输入邮箱收到的 6 位验证码')
      }
    }
  }

  const handleOtpSubmit = async () => {
    if (!otpSlot || !otpCode.trim()) return
    setOtpSubmitting(true)
    try {
      await apiFetch(`/tasks/${taskId}/submit-otp`, {
        method: 'POST',
        body: JSON.stringify({ slot: otpSlot, code: otpCode.trim() }),
      })
      message.success('验证码已提交')
      setOtpSlot(null)
      setOtpCode('')
    } catch (e: any) {
      message.error(e?.message || '提交失败')
    } finally {
      setOtpSubmitting(false)
    }
  }

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      message.success('日志已复制')
    } catch {
      message.error('复制失败')
    }
  }

  const handleSkipCurrent = async () => {
    if (isFinished) return
    setSkipLoading(true)
    try {
      const response = await apiFetch(`/tasks/${taskId}/skip-current`, { method: 'POST' }) as {
        control?: { targeted_skip_attempts?: number }
      }
      const targeted = Number(response.control?.targeted_skip_attempts || 0)
      message.success(
        targeted > 1
          ? `已发送跳过 ${targeted} 个进行中账号请求`
          : '已发送跳过当前账号请求',
      )
    } catch (error_: unknown) {
      const detail = error_ instanceof Error ? error_.message : '请求失败'
      message.error(detail)
    } finally {
      setSkipLoading(false)
    }
  }

  const handleStopTask = async () => {
    if (isFinished) return
    setStopLoading(true)
    try {
      await apiFetch(`/tasks/${taskId}/stop`, { method: 'POST' })
      setStopRequested(true)
      message.success('已发送停止任务请求，正在停止进行中的线程')
    } catch (error_: unknown) {
      const detail = error_ instanceof Error ? error_.message : '请求失败'
      message.error(detail)
    } finally {
      setStopLoading(false)
    }
  }

  useEffect(() => {
    onDoneRef.current = onDone
  }, [onDone])

  useEffect(() => {
    if (!taskId) return
    const controller = new AbortController()
    let cancelled = false
    const baseRetryMs = 1000
    const maxRetryMs = 8000
    nextSinceRef.current = 0
    setLines([])
    setError('')
    setTerminalStatus('idle')
    setStopRequested(false)

    const sleep = async (ms: number) =>
      new Promise((resolve) => setTimeout(resolve, ms))

    const initSnapshot = async (): Promise<boolean> => {
      try {
        const snapshot = await apiFetch(`/tasks/${taskId}`) as {
          logs?: string[]
          status?: TaskTerminalStatus | string
          control?: { stop_requested?: boolean }
        }
        if (cancelled) return true

        const snapshotLines = Array.isArray(snapshot.logs) ? snapshot.logs : []
        setLines(snapshotLines)
        nextSinceRef.current = snapshotLines.length
        // 检测快照中的 OTP 标记（任务已在等待中）
        for (const line of snapshotLines) {
          checkOtpRequired(line)
        }
        setStopRequested(Boolean(snapshot.control?.stop_requested))

        if (snapshot.status === 'done' || snapshot.status === 'failed' || snapshot.status === 'stopped') {
          setTerminalStatus(snapshot.status)
          onDoneRef.current?.()
          return true
        }
      } catch (error_: unknown) {
        if (!cancelled) {
          const detail = error_ instanceof Error ? error_.message : '获取任务快照失败'
          setError(detail)
        }
      }
      return false
    }

    const connectStreamOnce = async (): Promise<boolean> => {
      try {
        const token = getToken()
        const headers: Record<string, string> = {}
        if (token) headers.Authorization = `Bearer ${token}`

        const since = nextSinceRef.current
        const response = await fetch(`${API_BASE}/tasks/${taskId}/logs/stream?since=${since}`, {
          headers,
          signal: controller.signal,
        })

        if (!response.ok) {
          setError(`日志流连接失败 (${response.status})`)
          return true
        }

        if (!response.body) {
          setError('日志流未返回可读数据')
          return false
        }

        setError('')
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (!cancelled) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''

          for (const part of parts) {
            const match = part.match(/^data:\s*(.+)$/m)
            if (!match) continue
            try {
              const payload = JSON.parse(match[1]) as {
                line?: string
                done?: boolean
                status?: TaskTerminalStatus
              }
              if (payload.line) {
                nextSinceRef.current += 1
                setLines((previous) => [...previous, payload.line!])
                checkOtpRequired(payload.line!)
              }
              if (payload.done) {
                setTerminalStatus(payload.status || 'done')
                onDoneRef.current?.()
                return true
              }
            } catch {
              // ignore malformed SSE payload
            }
          }
        }

        return false
      } catch (error_: unknown) {
        if (!cancelled && !(error_ instanceof DOMException && error_.name === 'AbortError')) {
          return false
        }
        return true
      }
    }

    const connectStream = async () => {
      const shouldStopImmediately = await initSnapshot()
      if (shouldStopImmediately || cancelled) return

      let retryCount = 0
      while (!cancelled) {
        const shouldStop = await connectStreamOnce()
        if (shouldStop || cancelled) return

        retryCount += 1
        const retryMs = Math.min(baseRetryMs * (2 ** (retryCount - 1)), maxRetryMs)
        setError(`日志流连接中断，${retryMs / 1000}s 后重试（第 ${retryCount} 次）`)
        await sleep(retryMs)
      }
    }

    void connectStream()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [taskId])

  useEffect(() => {
    if (!panelRef.current) return
    panelRef.current.scrollTop = panelRef.current.scrollHeight
  }, [lines])

  const footerText =
    terminalStatus === 'done'
      ? { text: '注册完成', color: '#10b981' }
      : terminalStatus === 'stopped'
        ? { text: '任务已停止', color: '#d97706' }
        : terminalStatus === 'failed'
          ? { text: '任务失败', color: '#dc2626' }
          : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* OTP 验证码弹窗 */}
      <Modal
        open={!!otpSlot}
        title="请输入验证码"
        onOk={handleOtpSubmit}
        onCancel={() => { setOtpSlot(null); setOtpCode('') }}
        okText="提交"
        cancelText="取消"
        confirmLoading={otpSubmitting}
        okButtonProps={{ disabled: !otpCode.trim() }}
      >
        <p style={{ marginBottom: 12, color: '#6b7280', fontSize: 13 }}>{otpHint}</p>
        <Input
          autoFocus
          placeholder="请输入 6 位验证码"
          maxLength={6}
          value={otpCode}
          onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ''))}
          onPressEnter={handleOtpSubmit}
          style={{ letterSpacing: 6, textAlign: 'center', fontSize: 20, fontWeight: 'bold' }}
        />
      </Modal>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <Space>
          <Button
            size="small"
            icon={<FastForwardOutlined />}
            onClick={handleSkipCurrent}
            loading={skipLoading}
            disabled={isFinished}
          >
            跳过当前账号
          </Button>
          <Button
            size="small"
            danger
            icon={<StopOutlined />}
            onClick={handleStopTask}
            loading={stopLoading}
            disabled={isFinished}
          >
            停止任务
          </Button>
        </Space>
        <Button size="small" icon={<CopyOutlined />} onClick={handleCopyAll} disabled={lines.length === 0}>
          复制日志
        </Button>
      </div>

      <div
        ref={panelRef}
        className="log-panel"
        style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          background: '#ffffff',
          border: '1px solid #e5e7eb',
          borderRadius: 8,
          padding: 12,
          fontFamily: 'monospace',
          fontSize: 12,
          minHeight: 320,
          maxHeight: '65vh',
          userSelect: 'text',
          WebkitUserSelect: 'text',
          cursor: 'text',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {lines.length === 0 && !error && <div style={{ color: '#9ca3af' }}>等待日志...</div>}
        {error && <div style={{ color: '#dc2626' }}>{error}</div>}
        {lines.map((line, index) => (
          <div
            key={index}
            style={{
              lineHeight: 1.5,
              color:
                line.includes('✓') || line.includes('成功')
                  ? '#059669'
                  : line.includes('✗') || line.includes('失败') || line.includes('错误')
                    ? '#dc2626'
                    : line.includes('停止') || line.includes('跳过')
                      ? '#d97706'
                      : '#1f2937',
            }}
          >
            {line}
          </div>
        ))}
      </div>

      {footerText ? (
        <div style={{ fontSize: 12, color: footerText.color, marginTop: 8 }}>
          {footerText.text}
        </div>
      ) : null}
    </div>
  )
}

export default TaskLogPanel
