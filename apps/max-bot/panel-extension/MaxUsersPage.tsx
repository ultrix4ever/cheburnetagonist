import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'
import { StepUpModal } from './StepUpModal'
import './MaxUsersPage.css'

type Grant = { id: number; slots: number; starts: number; expires: number; source: string }
type Person = { id: number; name: string; max_id: string | null; active: boolean; free: boolean; phone_verified: boolean; registered: number | null; quota: number; limit: number; used: number; legacy: number; grants: Grant[]; phone: string; allowed_servers: number[] }
type Profile = { id: number; server_id: number; name: string; person_id: number | null; state: string; has_config: boolean; billing_state: string | null; suspended_at: number | null; legacy: number }
type Payment = { id: string; max_id: string; person_id: number; slots: number; amount: number; state: string; receipt_mid: string | null; grant_id: number | null; created: number; decided: number | null }
type Server = { id: number; name: string; online: boolean | null }
type Replacement = { id: string; max_id: number; old_id: number; new_id: number | null; state: string; expires: number }
type Job = { id: string; person_id: number; state: string; result: { revoked?: number; unconfirmed?: number[]; uncertain_operations?: string[] } | null }
type Audit = { id: number; at: number; action: string; target: string; max_id: number | null }
type Reach = { vantage: string; server_id: number; name: string; status: string; stage: string; checked: number; since: number; fails: number; rtt_ms: number | null; panel_online: number | null }
type Outage = { id: number; server_id: number; name: string; vantage: string; started: number; ended: number }
type Snapshot = { reachability?: Reach[]; outages?: Outage[]; people: Person[]; profiles: Profile[]; servers: Server[]; replacements: Replacement[]; jobs: Job[]; audit: Audit[]; operations: { id: string; max_id: number; action: string; state: string }[]; payments: Payment[]; billing: { price: number; period_days: number; max_slots: number } }
type Form = { id?: number; max_id: string; name: string; quota: number; phone: string; active: boolean; free: boolean; allowed_servers: number[] }
const paymentStatus: Record<string, string> = { awaiting: 'Ждём перевод', submitted: 'На проверке', confirmed: 'Подтверждён', rejected: 'Отклонён', cancelled: 'Отменён' }
const grantSource: Record<string, string> = { payment: 'оплата', manual: 'вручную', free_to_paid: 'переход с бесплатного' }
const day = (ts: number) => new Date(ts * 1000).toLocaleDateString('ru-RU')
const moment = (ts: number) => new Date(ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const span = (seconds: number) => { const m = Math.max(Math.floor(seconds / 60), 0); return m < 60 ? `${m} мин` : m < 2880 ? `${Math.floor(m / 60)} ч` : `${Math.floor(m / 1440)} дн` }
function reachText(r: Reach, now: number) {
  if (now - r.checked > 3 * 3600) return 'Нет свежих данных'
  if (r.status === 'ok') return '✅ Подключается' + (r.rtt_ms != null ? `, отклик ${r.rtt_ms} мс` : '')
  if (r.fails < 2) return 'Одна неудачная проверка, ждём подтверждения'
  return `⛔ Не подключается уже ${span(now - r.since)}` + (r.panel_online === 0 ? ' (панель сервер тоже не видит)' : '')
}
const keys = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? 'ключ' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'ключа' : 'ключей'}`
function paidText(p: Profile, free: boolean) {
  if (free) return 'Бесплатно'
  if (p.billing_state === 'suspended') return 'Остановлен за неоплату с ' + day(p.suspended_at ?? 0)
  if (p.billing_state === 'lapsed') return 'Сверх оплаченного (был на паузе)'
  if (p.legacy) return 'Старый, без оплаты'
  return 'В оплаченном лимите'
}
const status: Record<string, string> = { active: 'Работает', paused: 'На паузе', revoked: 'Отозван', missing: 'Не найден — нужна сверка', pending: 'В очереди', processing: 'Выполняется', done: 'Завершено', partial: 'Частично — нужна сверка', unknown: 'Результат не определён', ready: 'Ожидает переключения', committed: 'Замена завершена', cancelled: 'Замена отменена', creating: 'Создаётся', committing: 'Завершается', canceling: 'Отменяется' }
const actions: Record<string, string> = { payment_created: 'Платёж начат', self_registered: 'Зарегистрировался в боте', profile_renamed: 'Ключ переименован', payment_submitted: 'Платёж на проверке', payment_confirmed: 'Платёж подтверждён', payment_rejected: 'Платёж отклонён', grant_manual: 'Ключи добавлены вручную', grant_cancelled: 'Пакет ключей аннулирован', person_deleted: 'Пользователь удалён', revocation_queued: 'Отзыв поставлен в очередь', revocation_confirmed: 'Отзыв подтверждён', person_saved: 'Пользователь сохранён', person_updated: 'Пользователь изменён', profile_assigned: 'Ключ привязан', issue_started: 'Выдача начата', operation_done: 'Операция завершена', operation_unknown: 'Требуется сверка', replacement_started: 'Замена начата', replacement_ready: 'Новый ключ готов', replacement_committed: 'Замена подтверждена', replacement_cancelled: 'Замена отменена', replacement_unknown: 'Сбой замены — нужна сверка', revoke_all_queued: 'Отзыв всех ключей поставлен в очередь', revoke_all_started: 'Отзыв всех ключей начат', revoke_all_done: 'Все ключи отозваны', revoke_all_partial: 'Отзыв подтверждён не полностью', inventory_failed: 'Сервер недоступен при синхронизации', owner_identity_corrected: 'Исправлен MAX ID владельца' }

export function MaxUsersPage({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [data, setData] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [assignId, setAssignId] = useState('')
  const [grantSlots, setGrantSlots] = useState(1)
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null)
  const [pending, setPending] = useState<{ text: string; run: (password: string) => Promise<void> } | null>(null)
  const load = useCallback(async () => {
    try { setData(await api<Snapshot>('/api/max-users')); setError('') }
    catch (e) { if (e instanceof ApiError && e.status === 401) return onUnauthorized(); setError(e instanceof Error ? e.message : 'Не удалось загрузить пользователей') }
  }, [onUnauthorized])
  useEffect(() => { void load() }, [load])
  const inProgress = data?.jobs.some(j => j.state === 'pending' || j.state === 'processing')
  useEffect(() => { if (!inProgress) return; const timer = setInterval(() => void load(), 3000); return () => clearInterval(timer) }, [inProgress, load])
  const person = data?.people.find(p => p.id === selected)
  const serverName = (id: number) => data?.servers.find(s => s.id === id)?.name ?? `Сервер ${id}`
  const userName = (id: number) => data?.people.find(p => p.id === id)?.name ?? `#${id}`
  function personPaid(p: Person) {
    if (p.free) return 'Бесплатно'
    if (!p.grants.length) return p.used ? <span className="form-error">Не оплачено</span> : 'Не оплачено'
    return p.grants.map(g => `${keys(g.slots)} до ${day(g.expires)}`).join(', ')
  }
  const describePayment = (x: Payment) => `${keys(x.slots)} на ${data?.billing.period_days ?? 30} дней`
  const decide = (x: Payment, decision: 'confirm' | 'reject', text: string) => request(text, async password => {
    await api(`/api/max-users/payments/${x.id}/${decision}`, { method: 'POST', body: JSON.stringify({ password }) })
  })
  const request = (text: string, run: (password: string) => Promise<void>) => setPending({ text, run })
  const finish = async (password: string) => { if (!pending) return; try { await pending.run(password); setPending(null); await load() } catch (e) { if (e instanceof ApiError && e.status === 401) { onUnauthorized(); return }; throw e } }
  function save() {
    if (!form) return
    if (!form.name.trim() || !/^\d+$/.test(form.max_id) || BigInt(form.max_id) <= 0n || BigInt(form.max_id) > 9223372036854775807n || !Number.isInteger(form.quota) || form.quota < 0 || form.quota > 100) { setError('Проверьте имя, MAX ID и лимит от 0 до 100.'); return }
    const value = { ...form, name: form.name.trim() }
    request(value.id ? 'Сохранить права и лимит пользователя? Существующие ключи автоматически не удаляются.' : 'Разрешить указанному MAX ID управление своими VPN-ключами?', async password => {
      const body = { name: value.name, quota: value.quota, phone: value.phone, active: value.active, free: value.free, allowed_servers: value.allowed_servers, password, ...(value.id ? {} : { max_id: value.max_id }) }
      const result = await api<{ id: number }>(value.id ? `/api/max-users/people/${value.id}` : '/api/max-users/people', { method: value.id ? 'PUT' : 'POST', body: JSON.stringify(body) })
      setSelected(result.id); setForm(null)
    })
  }
  return <section className="max-users">
    <div className="page-head"><h2>Пользователи MAX</h2><div className="page-head-actions">
      <button className="ghost" onClick={() => void load()}>Обновить</button>
      <button className="ghost" onClick={() => request('Обновить инвентарь всех серверов? VPN-профили не изменяются.', async password => {
        const result = await api<{ failed_servers: number[] }>('/api/max-users/sync', { method: 'POST', body: JSON.stringify({ password }) }); if (result.failed_servers.length) throw new Error('Не удалось опросить серверы: ' + result.failed_servers.join(', ') + '. Предыдущие записи сохранены.')
      })}>Сверить с серверами</button>
      <button onClick={() => setForm({ name: '', max_id: '', quota: 5, phone: '', active: true, free: false, allowed_servers: data?.servers.map(s => s.id) ?? [] })}>Добавить пользователя</button>
    </div></div>
    <p className="muted">Пользователи регистрируются сами: пишут боту, делятся номером из MAX (✓ — номер подтверждён MAX) и вводят имя. Новый аккаунт платный, с 0 ключей. Имя, телефон, серверы, бесплатность и названия ключей можно менять здесь. Лимит общий для всех серверов; ключи на паузе тоже занимают место.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!!data?.reachability?.length && <div className="card max-reach">
      <h3>Доступность серверов из России</h3>
      <div className="max-table"><table><thead><tr><th>Сервер</th><th>Состояние</th><th>Проверено</th><th>Точка</th></tr></thead><tbody>
        {data.reachability.map(r => <tr key={r.vantage + r.server_id}><td>{serverName(r.server_id)}</td><td>{reachText(r, Date.now() / 1000)}</td><td>{moment(r.checked)}</td><td>{r.vantage}</td></tr>)}
      </tbody></table></div>
      {!!data.outages?.length && <p className="muted small">Прошлые блокировки: {data.outages.map(o => `${serverName(o.server_id)} — ${span(o.ended - o.started)} (${day(o.started)}–${day(o.ended)})`).join('; ')}</p>}
      <p className="muted small">Настоящее VPN-подключение с сервера в России, раз в 15 минут. Блокировка адреса обычно снимается сама за несколько дней.</p>
    </div>}
    {form && <form className="card max-user-form" onSubmit={e => { e.preventDefault(); save() }}>
      <h3>{form.id ? 'Редактировать пользователя' : 'Новый пользователь'}</h3>
      <div className="max-fields">
        <label>Имя<input required maxLength={100} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
        <label>Числовой MAX ID<input required inputMode="numeric" pattern="[0-9]+" disabled={!!form.id} value={form.max_id} onChange={e => setForm({ ...form, max_id: e.target.value.trim() })} /></label>
        <label>{form.free ? 'Лимит ключей на всех серверах' : 'Лимит после снятия бесплатности (3 дня)'}<input required type="number" min={0} max={100} value={form.quota} onChange={e => setForm({ ...form, quota: Number(e.target.value) })} /></label>
        <label>Телефон{form.id && data?.people.find(x => x.id === form.id)?.phone_verified && form.phone === data?.people.find(x => x.id === form.id)?.phone ? ' (подтверждён в MAX)' : ''}<input type="tel" maxLength={40} value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} /></label>
      </div>
      <fieldset><legend>Доступные серверы для новых ключей и переноса</legend>{data?.servers.map(s => <label className="max-check" key={s.id}><input type="checkbox" checked={form.allowed_servers.includes(s.id)} onChange={e => setForm({ ...form, allowed_servers: e.target.checked ? [...form.allowed_servers, s.id] : form.allowed_servers.filter(id => id !== s.id) })} />{s.name}</label>)}</fieldset>
      <label className="max-check"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />Разрешить самообслуживание через бота</label>
      <label className="max-check"><input type="checkbox" checked={form.free} onChange={e => setForm({ ...form, free: e.target.checked })} />Бесплатный аккаунт — ключи без оплаты</label>
      <p className="muted small">У платного пользователя лимит = оплаченные ключи: {data?.billing.price ?? 100} ₽ за ключ на {data?.billing.period_days ?? 30} дней со дня подтверждения. Ключи в пределах лимита он получает сам, сразу. Поле лимита действует только для бесплатного аккаунта; если снять отметку, этот лимит сохраняется ещё 3 дня, чтобы человек успел оплатить.</p>
      <p className="muted small">Снятие разрешения не отзывает VPN-ключи. Для полного отключения используйте отдельную кнопку в карточке пользователя.</p>
      <div className="max-actions"><button type="submit">Сохранить</button><button className="ghost" type="button" onClick={() => setForm(null)}>Отмена</button></div>
    </form>}
    {!data ? <p>Загрузка…</p> : <>
      <div className="card max-table"><table><thead><tr><th>Пользователь / MAX ID</th><th>Бот</th><th>Ключи / лимит</th><th>Оплата</th><th>Разрешённые серверы</th><th>Действия</th></tr></thead><tbody>
        {data.people.map(p => <tr key={p.id} className={selected === p.id ? 'max-selected' : ''}><td><button className="ghost" onClick={() => { setSelected(p.id); setAssignId('') }}>{p.name}</button><div className="muted small">{p.max_id ?? 'Не привязан'}{p.phone && ` · ${p.phone}`}{p.phone && (p.phone_verified ? ' ✓' : ' (не подтверждён)')}{p.registered && ` · сам, ${day(p.registered)}`}</div></td><td>{p.active ? 'Разрешён' : 'Заблокирован'}</td><td><strong>{p.used} / {p.limit}</strong>{p.used > p.limit && <div className="form-error small">{p.free ? 'Создание новых ключей запрещено' : 'Сверх оплаты — новые ключи остановлены'}</div>}{!!p.legacy && <div className="muted small">+ старых без оплаты: {p.legacy}</div>}</td><td>{personPaid(p)}</td><td>{p.allowed_servers.map(serverName).join(', ') || 'Нет'}</td><td><button className="ghost" onClick={() => setForm({ ...p, active: !!p.active, free: !!p.free, max_id: p.max_id ?? '' })}>Изменить</button></td></tr>)}
        {!data.people.length && <tr><td colSpan={6}>Пользователи ещё не добавлены.</td></tr>}
      </tbody></table></div>
      {person && <div className="card"><h3>{person.name}: ключи на всех серверах</h3>
        <p>Занято {person.used} из {person.limit}.{person.free ? ' Снижение лимита и изменение списка серверов не удаляют существующие ключи.' : ' Лимит — сумма оплаченных пакетов; когда пакет заканчивается, самые новые ключи сверх лимита встают на паузу, через 30 дней без оплаты удаляются.'}{!!person.legacy && ` Ещё ${person.legacy} старых ключей работают без оплаты.`}</p>
        <div className="max-actions"><button className="ghost" onClick={() => request(person.active ? 'Запретить только общение с ботом? VPN продолжит работать.' : 'Вернуть пользователю доступ к боту?', async password => {
          await api(`/api/max-users/people/${person.id}`, { method: 'PUT', body: JSON.stringify({ ...person, active: !person.active, password }) })
        })}>{person.active ? 'Заблокировать только бота' : 'Разрешить бота'}</button>
        <button className="danger" onClick={() => request('ОТОЗВАТЬ ВСЕ VPN-КЛЮЧИ пользователя ' + person.name + ' на всех серверах и заблокировать бота? Это необратимо. Результат отзыва появится в журнале заданий ниже.', async password => {
          await api(`/api/max-users/people/${person.id}/revoke-all`, { method: 'POST', body: JSON.stringify({ password }) })
        })}>Отозвать весь VPN-доступ</button>
        {(() => {
          const live = data.profiles.filter(p => p.person_id === person.id && p.state !== 'revoked').length
          return <button className="danger" disabled={!!live} title={live ? 'Сначала отзовите все ключи и дождитесь подтверждения' : ''} onClick={() => request(`УДАЛИТЬ пользователя ${person.name} полностью? Будут стёрты профиль, телефон, оплаты, пакеты ключей и журнал его действий. Это необратимо. Если человек снова напишет боту, он сможет зарегистрироваться заново.`, async password => {
            await api(`/api/max-users/people/${person.id}/delete`, { method: 'POST', body: JSON.stringify({ password }) }); setSelected(null)
          })}>Удалить пользователя{live ? ` (сначала отзовите ключи: ${live})` : ''}</button>
        })()}</div>
        <div className="max-table"><table><thead><tr><th>Ключ</th><th>Сервер</th><th>Состояние</th><th>Оплата</th><th>Конфиг</th></tr></thead><tbody>{data.profiles.filter(p => p.person_id === person.id).map(p => <tr key={p.id}><td>{renaming?.id === p.id
          ? <form className="max-actions" onSubmit={e => { e.preventDefault(); const name = renaming.name.trim(); if (!/^[\p{L}\p{N}_ .,()-]{1,40}$/u.test(name)) { setError('Название ключа: до 40 символов, буквы, цифры, пробел и . , - ( )'); return } request(`Переименовать ключ #${p.id} в «${name}»? Имя изменится на сервере и в приложении пользователя после обновления.`, async password => {
              await api(`/api/max-users/profiles/${p.id}/rename`, { method: 'POST', body: JSON.stringify({ name, password }) }); setRenaming(null)
            }) }}><input autoFocus maxLength={40} value={renaming.name} onChange={e => setRenaming({ id: p.id, name: e.target.value })} /><button type="submit">Сохранить</button><button type="button" className="ghost" onClick={() => setRenaming(null)}>Отмена</button></form>
          : <>#{p.id} {p.name.split(' [max-')[0]}{['active', 'paused'].includes(p.state) && <button className="ghost small" onClick={() => setRenaming({ id: p.id, name: p.name.split(' [max-')[0] })}>Переименовать</button>}</>}</td><td>{serverName(p.server_id)}</td><td>{status[p.state] ?? p.state}</td><td>{['active', 'paused'].includes(p.state) ? paidText(p, person.free) : '—'}</td><td>{p.has_config ? 'Сохранён в панели' : 'Старый конфиг отсутствует'}</td></tr>)}</tbody></table></div>
        {!person.free && <>
          {!!person.grants.length && <div className="max-table"><table><thead><tr><th>Пакет</th><th>Ключей</th><th>Действует</th><th></th></tr></thead><tbody>
            {person.grants.map(g => <tr key={g.id}><td>{grantSource[g.source] ?? g.source}</td><td>{g.slots}</td><td>{day(g.starts)} — {day(g.expires)}</td><td><button className="ghost" onClick={() => request(`Аннулировать пакет: ${keys(g.slots)}? Лимит сразу уменьшится, лишние новые ключи встанут на паузу.`, async password => {
              await api(`/api/max-users/grants/${g.id}/cancel`, { method: 'POST', body: JSON.stringify({ password }) })
            })}>Аннулировать</button></td></tr>)}
          </tbody></table></div>}
          <div className="max-actions" style={{ alignItems: 'end' }}><label>Добавить оплаченные ключи вручную<input type="number" min={1} max={data.billing.max_slots} value={grantSlots} onChange={e => setGrantSlots(Number(e.target.value))} /></label>
            <button className="ghost" disabled={!Number.isInteger(grantSlots) || grantSlots < 1 || grantSlots > data.billing.max_slots} onClick={() => request(`Добавить ${person.name} ${keys(grantSlots)} на ${data.billing.period_days} дней? Используйте для оплаты, полученной вне бота. Остановленные за неоплату ключи включатся в пределах лимита.`, async password => {
              await api(`/api/max-users/people/${person.id}/grant`, { method: 'POST', body: JSON.stringify({ slots: grantSlots, password }) })
            })}>Добавить</button></div>
        </>}
        <label>Привязать существующий ключ<select value={assignId} onChange={e => setAssignId(e.target.value)}><option value="">Выберите профиль без владельца</option>{data.profiles.filter(p => p.person_id === null && ['active', 'paused'].includes(p.state)).map(p => <option key={p.id} value={p.id}>#{p.id} {p.name} — {serverName(p.server_id)}</option>)}</select></label>
        <button disabled={!assignId} onClick={() => request('Подтвердите, что этот ключ принадлежит выбранному человеку. Имя профиля само по себе этого не доказывает. Ключ займёт место в его лимите.', async password => {
          await api(`/api/max-users/profiles/${assignId}/assign`, { method: 'POST', body: JSON.stringify({ person_id: person.id, password }) }); setAssignId('')
        })}>Привязать ключ</button>
      </div>}
      {data.payments.some(x => ['submitted', 'awaiting'].includes(x.state)) && <div className="card"><h3>Платежи, ждущие решения</h3>
        <p className="muted small">Сверьте поступление в банке по коду из комментария к переводу. Подтверждение сразу увеличивает лимит ключей на {data.billing.period_days} дней.</p>
        {data.payments.filter(x => ['submitted', 'awaiting'].includes(x.state)).map(x => <div key={x.id} className="max-job">
          <strong>{x.id}</strong> · {userName(x.person_id)} · {describePayment(x)} · <strong>{x.amount} ₽</strong> · {paymentStatus[x.state] ?? x.state}{x.receipt_mid ? ' · платёжка в MAX' : ''}
          <div className="max-actions"><button onClick={() => decide(x, 'confirm', `Подтвердить получение ${x.amount} ₽ по коду ${x.id}? Лимит ${userName(x.person_id)} увеличится на ${keys(x.slots)}.`)}>Подтвердить</button>
            <button className="ghost" onClick={() => decide(x, 'reject', `Отклонить платёж ${x.id}? Пользователь получит уведомление.`)}>Отклонить</button></div>
        </div>)}</div>}
      {!!data.payments.length && <details className="card"><summary>История платежей</summary><div className="max-table"><table><thead><tr><th>Создан</th><th>Код</th><th>Пользователь</th><th>Что</th><th>Сумма</th><th>Статус</th></tr></thead><tbody>
        {data.payments.map(x => <tr key={x.id}><td>{new Date(x.created * 1000).toLocaleString('ru-RU')}</td><td>{x.id}</td><td>{userName(x.person_id)}</td><td>{describePayment(x)}</td><td>{x.amount} ₽</td><td>{paymentStatus[x.state] ?? x.state}</td></tr>)}
      </tbody></table></div></details>}
      {!!data.jobs.length && <div className="card"><h3>Отзыв всего VPN-доступа</h3>{data.jobs.slice(0, 10).map(j => <div key={j.id} className="max-job"><strong>{userName(j.person_id)}</strong> — {status[j.state] ?? j.state}{j.result && <span> · подтверждено отзывов: {j.result.revoked ?? 0}{!!j.result.unconfirmed?.length && ' · не подтверждены профили: ' + j.result.unconfirmed.map(id => '#' + id).join(', ')}{!!j.result.uncertain_operations?.length && ' · есть неопределённые операции — нужна сверка'}</span>}</div>)}</div>}
      {!!data.replacements.length && <details className="card"><summary>Переносы и перевыпуски</summary>{data.replacements.slice(0, 30).map(r => <p key={r.id}>MAX {String(r.max_id)}: #{r.old_id} → {r.new_id ? '#' + r.new_id : 'новый ключ ещё не подтверждён'} · {status[r.state] ?? r.state}{r.state === 'ready' && ' · срок до ' + new Date(r.expires * 1000).toLocaleTimeString('ru-RU')}</p>)}</details>}
      {!!data.operations.length && <div className="card"><h3>Операции, требующие сверки</h3><p className="form-error">Не повторяйте выдачу, пока не проверен фактический результат на сервере.</p>{data.operations.map(o => <p key={o.id}>MAX {String(o.max_id)} · {o.action} · {status[o.state] ?? o.state} · {o.id}</p>)}</div>}
      <details className="card"><summary>Журнал реестра (последние 100 событий)</summary><div className="max-table"><table><thead><tr><th>Время</th><th>Действие</th><th>Объект</th><th>MAX ID</th></tr></thead><tbody>{data.audit.map(a => <tr key={a.id}><td>{new Date(a.at * 1000).toLocaleString('ru-RU')}</td><td>{actions[a.action] ?? a.action}</td><td>{a.target}</td><td>{a.max_id === null ? 'Администратор / система' : String(a.max_id)}</td></tr>)}</tbody></table></div></details>
    </>}
    {pending && <StepUpModal text={pending.text} onSubmit={finish} onCancel={() => setPending(null)} />}
  </section>
}
