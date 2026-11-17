import { describe, expect, it } from 'vitest'
import { fixtureInstance, fixtureTask } from '../../test/fixtures'
import type { ExitInfo, Instance, Task } from '../api/client'
import {
  activeTaskFor, diagnosticLabel, diagnosticTone, hasPendingExitChange, healthState,
  instanceState, isActive, isFinished, progressText, proxyState, proxySummary,
  runState, startFailureFor, stepText,
} from './status'

const exit: ExitInfo = { status: 'ok', ip: '192.0.2.1', detail: '', checked_at: 'fixture' }
const task = (kind: string, state: string, id = 'a'): Task =>
  ({ ...fixtureTask(kind, 'source'), instance_id: id, state })
const instance = (changes: Partial<Instance> = {}) => fixtureInstance('a', 'node', changes)
const monitor: NonNullable<Instance['monitor']> = {
  enabled: true, state: 'healthy', detail: '', fault_since: null,
  recovery_attempts: 0, next_retry_at: null, checked_at: null,
}

describe('任务和出口提醒', () => {
  it.each(['queued', 'running', 'succeeded', 'failed', 'interrupted'])('识别 %s 的活动/终态', (state) => {
    expect(isActive(task('start', state))).toBe(['queued', 'running'].includes(state))
    expect(isFinished(task('start', state))).toBe(['succeeded', 'failed', 'interrupted'].includes(state))
  })
  it('仅取当前实例的最新活动任务，后续停止任务覆盖旧启动失败', () => {
    const failed = task('start', 'failed')
    const active = task('stop', 'queued')
    expect(activeTaskFor([task('start', 'running', 'other'), active, failed], 'a')).toBe(active)
    expect(startFailureFor(instance({ runtime: 'stopped' }), [failed])).toBe(failed)
    expect(startFailureFor(instance({ runtime: 'stopped' }), [active, failed])).toBeNull()
    expect(startFailureFor(instance(), [failed])).toBeNull()
  })
  it('队列步骤有占位，节点进度覆盖步骤文字', () => {
    const queued = task('start', 'queued')
    expect(stepText(queued)).toBe('排队中 · 第 1/1 步 · fixture')
    const running = { ...queued, state: 'running', phases: [
      { name: '第一步', state: 'done' }, { name: '第二步', state: 'current' },
    ] }
    expect(stepText(running)).toBe('第 2/2 步 · fixture')
    expect(progressText({ ...running, progress: { completed: 2, total: 5, label: '节点甲' } }))
      .toBe('已完成 2/5，当前：节点甲')
  })
  it('出口提醒依赖变化 ID，关闭旧提醒不隐藏新的变化', () => {
    expect(hasPendingExitChange(null)).toBe(false)
    expect(hasPendingExitChange(exit)).toBe(false)
    expect(hasPendingExitChange({ ...exit, change_id: 'old', dismissed_change_id: 'old' })).toBe(false)
    expect(hasPendingExitChange({ ...exit, change_id: 'new', dismissed_change_id: 'old' })).toBe(true)
  })
})

describe('实例状态的优先级', () => {
  it('无法确认 Docker 时不沿用活动任务或旧健康状态', () => {
    expect(runState(instance(), [task('start', 'running')], false).label).toBe('状态未知')
    expect(runState(instance({ runtime: 'unknown' }), [], true).label).toBe('状态未知')
    expect(proxyState(instance(), [], false).label).toBe('Docker 未连接')
    expect(healthState(instance({ monitor }), false).label).toBe('状态未知')
  })
  it('运行状态区分排队、部分运行、启动失败和停止', () => {
    expect(runState(instance(), [task('start', 'queued')], true).label).toBe('启动中')
    expect(runState(instance({ runtime: 'degraded' }), [], true).label).toBe('部分运行')
    expect(runState(instance({ runtime: 'stopped' }), [task('start', 'failed')], true).label).toBe('启动失败')
    expect(runState(instance({ runtime: 'stopped' }), [], true).label).toBe('已停止')
  })
  it('运行中异常优先显示，停止后的旧成功结果不显示为当前正常', () => {
    expect(proxyState(instance({ exit: { ...exit, status: 'failed' } }), [], true).label).toBe('代理异常')
    expect(proxyState(instance({ exit, runtime: 'stopped' }), [], true).label).toBe('最近一次检测正常')
    expect(proxyState(instance({ exit }), [], true, true).label).toBe('检测中')
    expect(proxyState(instance({ proxy: { mode: 'none', label: '' } }), [], true).label).toBe('未配置代理')
  })
  it('监测服务中断和异常覆盖旧的健康结论', () => {
    const row = instance({ monitor })
    expect(healthState(row, true, { running: false, error: '', checked_at: null }).label).toBe('监测已中断')
    expect(healthState(row, true, { running: true, error: 'fixture', checked_at: null }).label).toBe('监测异常')
    expect(healthState(row, true).label).toBe('监测正常')
  })
  it('汇总状态先呈现停止，再呈现恢复终止、代理故障或未检测', () => {
    expect(instanceState(instance({ runtime: 'stopped', monitor }), [], true).label).toBe('已停止')
    expect(instanceState(instance({ monitor: { ...monitor, state: 'failed' }, exit }), [], true).label).toBe('恢复已停止')
    expect(instanceState(instance({ monitor, exit: { ...exit, status: 'failed' } }), [], true).label).toBe('代理异常')
    expect(instanceState(instance({ monitor }), [], true).label).toBe('出口未检测')
  })
  it('代理摘要和诊断标签保留未知值', () => {
    expect(proxySummary(instance({ proxy: { mode: 'socks', label: '', host: '192.0.2.1', port: 1080 } })))
      .toEqual({ node: '192.0.2.1:1080', source: '手动 SOCKS5' })
    expect(proxySummary(instance()).source).toBe('订阅 · 已删除的订阅')
    expect(diagnosticLabel('unhealthy')).toBe('健康检查失败')
    expect(diagnosticLabel('new-state')).toBe('new-state')
    expect(diagnosticTone('unhealthy')).toBe('danger')
    expect(diagnosticTone('starting')).toBe('warn')
    expect(diagnosticTone('absent')).toBe('neutral')
  })
})
