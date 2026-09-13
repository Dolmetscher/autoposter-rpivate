import React, { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Account } from '../lib/types'
import RedDigests from './red/RedDigests'
import RedFeed from './red/RedFeed'
import RedGuidelines from './red/RedGuidelines'
import RedSources from './red/RedSources'

const TABS = [
  { id: 'sources', label: '📡 Источники' },
  { id: 'feed', label: '🗞 Лента' },
  { id: 'digests', label: '📰 Дайджесты' },
  { id: 'guidelines', label: '📐 Гайдлайны' },
]

export default function Redakciya({ accounts }: { accounts: Account[] }) {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'sources'
  return (
    <>
      <div className="between">
        <div>
          <h1>Автоматизированная редакция</h1>
          <p className="subtitle">
            Мониторинг источников → AI-скоринг по гайдлайнам → алерты в TG-бот →
            дайджесты с одобрением → автопостинг
          </p>
        </div>
      </div>
      <div className="row" style={{ gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        {TABS.map((t) => (
          <button key={t.id}
                  className={tab === t.id ? '' : 'secondary'}
                  onClick={() => setParams({ tab: t.id })}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'sources' && <RedSources />}
      {tab === 'feed' && <RedFeed />}
      {tab === 'digests' && <RedDigests accounts={accounts} />}
      {tab === 'guidelines' && <RedGuidelines />}
    </>
  )
}
