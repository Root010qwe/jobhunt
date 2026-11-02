import { Bar, Count, Field, Icon, Stage, States, Text, Window, type Act } from '../../kit'
import './jobhunt.css'

// JobHunt: the bot found a vacancy, one click opens the company's form,
// the extension fills it and attaches the tailored resume, sent → «Подано» rolls.

type S = {
  pressed: boolean
  queued: boolean
  form: boolean
  name: string
  email: string
  phone: string
  letter: string
  file: boolean
  focus: '' | 'name' | 'email' | 'phone' | 'letter'
  sent: boolean
  applied: number
}

const initial: S = {
  pressed: false, queued: false, form: false,
  name: '', email: '', phone: '', letter: '', file: false, focus: '',
  sent: false, applied: 13,
}

const NAME = 'Анна Петрова'
const EMAIL = 'anna@example.com'
const PHONE = '+7 900 000-00-00'
const LETTER = 'Здравствуйте! Откликаюсь на Junior PM в Авито: запускала онбординг, +12 % конверсии в покупку.'

const POSTER: S = {
  ...initial, queued: true, form: true, name: NAME, email: EMAIL, phone: PHONE, letter: LETTER,
  file: true, sent: true, applied: 14,
}

async function script(a: Act<S>) {
  await a.wait(700)
  // 1. One click on the vacancy the bot found
  await a.move('apply')
  await a.wait(250)
  await a.click()
  await a.set({ queued: true })
  await a.wait(350)
  // 2. The company's form comes forward
  await a.set({ form: true })
  await a.wait(700)
  // 3. The extension fills it: no cursor, the fields arrive one after another
  await a.set({ name: NAME })
  await a.wait(260)
  await a.set({ phone: PHONE })
  await a.wait(260)
  await a.set({ email: EMAIL })
  await a.wait(260)
  await a.set({ letter: LETTER })
  await a.wait(420)
  await a.set({ file: true })
  await a.wait(900)
  // 4. Sent from the form's own button
  await a.move('send')
  await a.wait(250)
  await a.click()
  await a.set({ sent: true })
  await a.wait(500)
  // 5. The tracker counts it
  await a.set({ applied: 14 })
  await a.move('rest')
  await a.wait(2600)
  // Reset: the form leaves first, then the tracker settles back
  await a.set({ form: false })
  await a.wait(500)
  await a.set({ ...initial, applied: 14 })
  await a.snap({ applied: 13 })
  await a.wait(300)
}

const ROWS = [
  { co: 'Ozon', role: 'Стажёр — менеджер продукта', meta: 'без опыта · Москва', score: 41 },
  { co: 'Авито', role: 'Junior Product Manager', meta: '1–3 года · Москва', score: 38, focal: true },
  { co: 'Т-Банк', role: 'Младший продакт-менеджер', meta: 'без опыта · гибрид', score: 35 },
]

export function JobHuntHero() {
  return (
    <Stage width={900} height={440} initial={initial} poster={POSTER} script={script}
      cursor={{ at: { wide: { x: 360, y: 400 }, compact: { x: 200, y: 360 } } }}
      accent="#2F45C5" className="jh">
      {(s) => (
        <>
          <Window url="localhost:8765" className="jh-app" receded={false}>
            <div className="jh-body">
              <div className="jh-tabs">
                <span className="jh-tab">Отклики <b className="jh-n"><Count value={s.applied} /></b></span>
                <span className="jh-tab is-on">Куда откликнуться <b className="jh-badge">24</b></span>
                <span className="jh-tab"><Text>Резюме</Text></span>
              </div>
              <div className="jh-head">
                <div className="jh-title">Куда откликнуться</div>
                <div className="jh-sub">Найдено сегодня · hh.ru и 22 карьерных сайта</div>
              </div>
              <div className="jh-list">
                {ROWS.map((r) => (
                  <div key={r.co} className={'jh-row' + (r.focal ? ' is-focal' : '')}>
                    <div className="jh-main">
                      <div className="jh-role">{r.role}</div>
                      <div className="jh-meta"><span className="jh-co">{r.co}</span>{r.meta}<span className="jh-score">{r.score}</span></div>
                    </div>
                    {r.focal ? (
                      <div className="jh-btn" data-target="apply" data-cursor="pointer" data-on={s.queued}>
                        <States value={s.sent ? 'sent' : s.queued ? 'queued' : 'idle'} states={{
                          idle: <span>Откликнуться</span>,
                          queued: <span>Заполняю форму…</span>,
                          sent: <span className="jh-ok"><Icon name="check" size={14} />Подано</span>,
                        }} />
                      </div>
                    ) : (
                      <Bar w={104} h={30} r={8} tone={2} />
                    )}
                  </div>
                ))}
                {[64, 52].map((w, i) => (
                  <div key={i} className="jh-row jh-ghost">
                    <div className="jh-main"><Bar w={w * 3} h={8} tone={2} /><Bar w={w * 2} h={6} tone={1} style={{ marginTop: 9 }} /></div>
                    <Bar w={104} h={30} r={8} tone={1} />
                  </div>
                ))}
              </div>
            </div>
          </Window>

          <Window chrome="hairline" url="career.avito.com/vacancies/junior-pm" className="jh-form" data-show={s.form}>
            <div className="jh-fbody">
              <div className="jh-ftitle">Junior Product Manager</div>
              <div className="jh-grid">
                <Field label="Имя и фамилия" value={s.name} />
                <Field label="Телефон" value={s.phone} />
                <Field className="jh-wide" label="Email" value={s.email} />
              </div>
              <div className="jh-letter" data-filled={!!s.letter}>
                <div className="jh-llabel">Сопроводительное письмо</div>
                <div className="jh-ltext"><span className="jh-lfill" data-show={!!s.letter}>{LETTER}</span></div>
              </div>
              <div className="jh-fbottom">
                <div className="jh-file" data-show={s.file}>
                  <Icon name="file" size={14} />Резюме_Петрова_Авито.pdf
                </div>
                <div className="jh-send" data-target="send" data-cursor="pointer" data-sent={s.sent}>
                  <States value={s.sent ? 'sent' : 'send'} states={{
                    send: <span>Отправить</span>,
                    sent: <span className="jh-ok"><Icon name="check" size={14} />Отправлено</span>,
                  }} />
                </div>
              </div>
            </div>
          </Window>
          <span data-target="rest" style={{ position: 'absolute', left: 360, top: 400 }} />
        </>
      )}
    </Stage>
  )
}
