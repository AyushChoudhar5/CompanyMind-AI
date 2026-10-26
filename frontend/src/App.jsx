import { useEffect, useRef, useState } from 'react'
import './App.css'
import DocumentAdmin from './DocumentAdmin'
import DocumentLibrary from './DocumentLibrary'

function Icon({ name, size = 20, ...props }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M12 19V5m-6 6 6-6 6 6" />,
    book: <><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Z" /><path d="M12 6v14" /></>,
    file: <><path d="M14 3H5v18h14V8Z" /><path d="M14 3v5h5M8 12h8M8 16h6" /></>,
    chat: <path d="M20 11a8 8 0 0 1-8 8H4l-1 2V11a8 8 0 0 1 17 0Z" />,
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1" /></>,
    calendar: <><rect x="4" y="5" width="16" height="16" rx="2" /><path d="M16 3v4M8 3v4M4 11h16M8 15h3" /></>,
    copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M15 8V3H3v13h5" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.file}</svg>
}

const suggestions = [
  { icon: 'book', title: 'Meet your company', question: 'What does TechNova do, and who are its customers?' },
  { icon: 'file', title: 'Explore our products', question: 'What products does TechNova offer?' },
  { icon: 'check', title: 'Get IT help', question: 'How do I request access to company software?' },
]

function AnswerText({ text, sources, onReference }) {
  return text.split(/(\*\*[^*]+\*\*|\[\d+\]|【\d+】)/g).map((part, index) => {
    const reference = part.match(/^(?:\[(\d+)\]|【(\d+)】)$/)
    if (reference) {
      const number = Number(reference[1] || reference[2])
      if (sources.some(source => source.reference === number)) {
        return <button className="citation" key={index} onClick={() => onReference(number)} aria-label={'Read source passage ' + number}>{number}</button>
      }
    }
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    return <span key={index}>{part}</span>
  })
}

function KnowledgeWorkspace({ session, onSignOut }) {
  const [question, setQuestion] = useState('')
  const [showAdmin, setShowAdmin] = useState(false)
  const [showLibrary, setShowLibrary] = useState(false)
  const [libraryRevision, setLibraryRevision] = useState(0)
  const [library, setLibrary] = useState([])
  const [libraryError, setLibraryError] = useState('')
  const [selectedDocument, setSelectedDocument] = useState('')
  const [conversationId, setConversationId] = useState(null)
  const [conversationThreadId, setConversationThreadId] = useState(null)
  const [conversationNotice, setConversationNotice] = useState('')
  const [entries, setEntries] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [pending, setPending] = useState(false)
  const [health, setHealth] = useState('checking')
  const [showSources, setShowSources] = useState(true)
  const [sourceId, setSourceId] = useState(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [copyStatus, setCopyStatus] = useState('')
  const textarea = useRef(null)
  const requestRef = useRef(null)
  const active = entries.find(entry => entry.id === activeId)
  const sources = active?.sources || []
  const otherTurns = active ? entries.filter(entry => entry.threadId === active.threadId && entry.id !== active.id && !entry.error).slice().reverse() : []
  const viewingEarlierAnswer = active && !active.error && entries.find(entry => entry.threadId === active.threadId && !entry.error)?.id !== active.id

  async function checkHealth(signal) {
    setHealth('checking')
    try {
      const response = await fetch('/api/health', { signal: signal || AbortSignal.timeout(5000) })
      const data = await response.json()
      setHealth(response.ok && data.status === 'ok' ? 'online' : 'offline')
    } catch {
      if (!signal?.aborted) setHealth('offline')
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)
    fetch('/api/health', { signal: controller.signal })
      .then(async response => {
        const data = await response.json()
        setHealth(response.ok && data.status === 'ok' ? 'online' : 'offline')
      })
      .catch(() => setHealth('offline'))
    return () => {
      clearTimeout(timeout)
      controller.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/documents', { headers: { Authorization: 'Bearer ' + session.access_token }, signal: controller.signal })
      .then(async response => {
        if (response.status === 401) { onSignOut(); return }
        if (!response.ok) throw new Error('Document list is unavailable.')
        const data = await response.json()
        setLibrary(data.documents)
        setSelectedDocument(current => data.documents.some(item => item.document_id === current) ? current : '')
        setLibraryError('')
      }).catch(error => { if (error.name !== 'AbortError') setLibraryError(error.message) })
    return () => controller.abort()
  }, [session, onSignOut, libraryRevision])

  useEffect(() => () => requestRef.current?.abort(), [])

  useEffect(() => {
    if (textarea.current) {
      textarea.current.style.height = 'auto'
      textarea.current.style.height = Math.min(textarea.current.scrollHeight, 180) + 'px'
    }
  }, [question])

  function freshConversation() {
    if (requestRef.current) return
    // IDs stay in this tab's memory. Older conversations can be resumed from history
    // until the server expires them; a fresh conversation never sends the old ID.
    setConversationId(null)
    setConversationThreadId(null)
    setConversationNotice('')
    setActiveId(null)
    setQuestion('')
    setSourceId(null)
    setMenuOpen(false)
    setCopyStatus('')
    textarea.current?.focus()
  }

  function selectEntry(entry) {
    if (requestRef.current) return
    setActiveId(entry.id)
    setConversationId(entry.conversationId || null)
    setConversationThreadId(entry.contextReset ? null : entry.threadId)
    setSelectedDocument(entry.documentId)
    setConversationNotice(entry.contextReset ? 'This conversation can no longer be continued. Ask a complete question to start a new one.' : '')
    setQuestion('')
    setSourceId(entry.sources[0]?.reference ?? null)
    setCopyStatus('')
    setMenuOpen(false)
  }

  function changeDocumentScope(value) {
    if (requestRef.current) return
    freshConversation()
    setSelectedDocument(value)
    setConversationNotice('Search scope changed. Ask a complete question to start a new conversation here.')
  }

  function returnFromAdmin() {
    freshConversation()
    setShowAdmin(false)
    setSelectedDocument('')
    setEntries([])
    setLibraryRevision(value => value + 1)
  }

  async function ask(event, suppliedQuestion) {
    event?.preventDefault()
    const text = (suppliedQuestion || question).trim()
    if (!text || text.length > 2000 || requestRef.current) return
    const id = crypto.randomUUID()
    const threadId = conversationThreadId || crypto.randomUUID()
    const requestedConversation = conversationId
    const documentId = selectedDocument
    const controller = new AbortController()
    requestRef.current = controller
    const timeout = setTimeout(() => controller.abort(), 95000)
    setEntries(previous => [{ id, threadId, conversationId: requestedConversation, documentId, question: text, sources: [], answer: '', error: '' }, ...previous].slice(0, 12))
    setConversationThreadId(threadId)
    setActiveId(id)
    setQuestion('')
    setPending(true)
    setSourceId(null)
    setCopyStatus('')
    setMenuOpen(false)
    setConversationNotice('')
    let resetContext = false
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + session.access_token },
        body: JSON.stringify({ question: text, top_k: 5, document_id: documentId || undefined, conversation_id: requestedConversation || undefined }),
        signal: controller.signal,
      })
      const data = await response.json().catch(() => null)
      if (response.status === 401) { onSignOut(); return }
      if (response.status === 409) {
        resetContext = true
        throw new Error(typeof data?.detail === 'string' ? data.detail : 'This conversation is unavailable. Ask a complete question to start a new conversation.')
      }
      if (!response.ok) {
        const detail = typeof data?.detail === 'string' ? data.detail : null
        throw new Error(detail || (response.status === 422 ? 'Please enter a question of 1–2,000 characters.' : 'The service could not complete this request. Check the backend and try again.'))
      }
      if (typeof data?.answer !== 'string' || !Array.isArray(data.sources) || typeof data.conversation_id !== 'string') {
        resetContext = true
        throw new Error('The server returned an unexpected response. Ask a complete question to start a new conversation.')
      }
      setHealth('online')
      setConversationId(data.conversation_id)
      setEntries(previous => previous.map(entry => entry.id === id ? { ...entry, conversationId: data.conversation_id, answer: data.answer, sources: data.sources, truncated: data.truncated } : entry))
      setShowSources(true)
      setSourceId(data.sources[0]?.reference ?? null)
    } catch (error) {
      // A lost response may already have updated server memory. Starting afresh
      // avoids continuing an invisible turn or submitting a follow-up twice.
      resetContext = resetContext || error.name === 'AbortError' || error instanceof TypeError
      const message = error.name === 'AbortError'
        ? 'This request took too long. The server may still be finishing it; try again shortly.'
        : error instanceof TypeError
          ? 'Could not reach CompanyMind. Check that your backend is running and try again.'
          : error.message
      setEntries(previous => previous.map(entry => entry.id === id ? { ...entry, error: message } : entry))
      if (resetContext) {
        setConversationId(null)
        setConversationThreadId(null)
        setEntries(previous => previous.map(entry => entry.threadId === threadId ? { ...entry, conversationId: null, contextReset: true } : entry))
        setConversationNotice('Conversation context was reset. Ask a complete question; your request has not been automatically resent.')
      }
    } finally {
      clearTimeout(timeout)
      requestRef.current = null
      setPending(false)
    }
  }

  function openReference(number) {
    setShowSources(true)
    setSourceId(number)
    setTimeout(() => document.getElementById('source-' + number)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
  }

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(active.answer)
      setCopyStatus('Copied')
    } catch {
      setCopyStatus('Select the answer to copy it')
    }
  }

  if (showLibrary) return <DocumentLibrary session={session} onSignOut={onSignOut}
    onBack={() => { setShowLibrary(false); setLibraryRevision(value => value + 1) }}
    onAsk={document => {
      changeDocumentScope(document.document_id)
      setShowLibrary(false)
      setLibraryRevision(value => value + 1)
      setQuestion('What should I know about "' + document.title + '"?')
    }} />

  if (showAdmin && session.user.role === 'hr_admin') return <DocumentAdmin session={session} onSignOut={onSignOut} onBack={returnFromAdmin} />

  return (
    <div className="workspace">
      {menuOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <aside className={'sidebar ' + (menuOpen ? 'is-open' : '')}>
        <a className="brand" href="#" onClick={event => { event.preventDefault(); if (!pending) freshConversation() }}>
          <span className="brand-symbol"><span /><span /><span /></span>
          <span>companymind<span className="brand-period">.</span></span>
        </a>
        <div className="workspace-label"><span className="workspace-monogram">CM</span><div>{session.user.username}<small>{session.user.role === 'hr_admin' ? 'HR administrator' : 'Employee workspace'}</small></div></div>
        <button className="new-question" onClick={freshConversation} disabled={pending}><Icon name="plus" size={17} />New conversation<span className="shortcut">+</span></button>
        <div className="nav-heading">WORKSPACE</div>
        <button className="nav-item selected" onClick={() => { setMenuOpen(false); textarea.current?.focus() }}><Icon name="chat" size={18} />Ask your documents<span className="nav-dot" /></button>
        <button className="nav-item" onClick={() => { setShowSources(value => !value); setMenuOpen(false) }}><Icon name="book" size={18} />Source passages</button>
        <button className="nav-item" onClick={() => { setShowLibrary(true); setMenuOpen(false) }} disabled={pending}><Icon name="file" size={18} />{session.user.role === 'hr_admin' ? 'Document library' : 'My documents'}</button>
        {session.user.role === 'hr_admin' && <button className="nav-item" onClick={() => { setShowAdmin(true); setMenuOpen(false) }} disabled={pending}><Icon name="file" size={18} />Manage documents</button>}
        <div className="history-heading nav-heading">RECENT QUESTIONS<span>{entries.length > 0 ? entries.length : ''}</span></div>
        <div className="history-list">
          {entries.length === 0 ? <p className="history-empty">Your questions will appear here.<br />A little less searching next time.</p> : entries.map(entry => (
            <button key={entry.id} className={'history-item ' + (activeId === entry.id ? 'active' : '')} disabled={pending} title={entry.question} onClick={() => selectEntry(entry)}><span className="history-marker" />{entry.question}</button>
          ))}
        </div>
        <div className="sidebar-bottom"><div>Signed in as {session.user.username}<small>Only documents your account can access.</small><button className="sign-out" onClick={onSignOut} disabled={pending}>Sign out</button></div></div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb"><button className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button><span>Workspace</span><span className="breadcrumb-slash">/</span><strong>Ask your documents</strong></div>
          <button className={'connection ' + health} onClick={() => checkHealth()} aria-label="Check backend connection"><span className="status-dot" />{health === 'online' ? 'Backend connected' : health === 'checking' ? 'Checking connection' : 'Backend unavailable'}<Icon name="refresh" size={13} /></button>
        </header>
        <div className={'content-layout ' + (!showSources ? 'sources-hidden' : '')}>
          <main className={'conversation ' + (active ? 'has-answer' : '')}>
            {!active ? (
              <section className="welcome">
                <div className="eyebrow"><span className="tiny-square" />THE ANSWER IS IN YOUR DOCUMENTS</div>
                <h1>Less searching.<br /><em>More understanding.</em></h1>
                <p className="welcome-description">Ask a question about your company’s knowledge.<br className="desktop-break" /> Get a clear answer, with the passages behind it.</p>
                <div className="suggestions">
                  {suggestions.map(item => <button className="suggestion" key={item.title} onClick={event => ask(event, item.question)} disabled={pending}><Icon name={item.icon} size={21} /><span>{item.title}</span><p>{item.question}</p><Icon name="chevron" size={14} className="suggestion-arrow" /></button>)}
                </div>
              </section>
            ) : (
              <section className="answer-area">
                {otherTurns.length > 0 && <details className="conversation-trail"><summary>{otherTurns.length} other {otherTurns.length === 1 ? 'question' : 'questions'} in this conversation</summary><div>{otherTurns.map(entry => <button key={entry.id} onClick={() => selectEntry(entry)} disabled={pending}><strong>{entry.question}</strong><span>{entry.answer.length > 150 ? entry.answer.slice(0, 150) + '…' : entry.answer}</span></button>)}</div></details>}
                <div className="eyebrow">YOUR QUESTION</div>
                <h1 className="question-title">{active.question}</h1>
                <div className="answer-label"><span className="answer-mark">cm</span><strong>CompanyMind</strong><span>DOCUMENT ANSWER</span></div>
                <div aria-live="polite" aria-busy={pending}>
                  {pending ? <div className="thinking"><span className="thinking-dot" />Finding evidence and preparing your answer…<div className="skeleton long" /><div className="skeleton" /><div className="skeleton short" /></div>
                    : active.error ? <div className="error-card" role="alert"><strong>We couldn’t complete this question.</strong><p>{active.error}</p><button className="text-button" onClick={() => { setQuestion(active.question); textarea.current?.focus() }}>Edit this question <Icon name="refresh" size={14} /></button></div>
                    : <><div className="answer-text"><AnswerText text={active.answer} sources={sources} onReference={openReference} /></div>{active.truncated && <p className="notice">This answer reached its length limit and may be incomplete.</p>}<div className="answer-actions"><button onClick={copyAnswer}><Icon name="copy" size={15} />{copyStatus || 'Copy answer'}</button><button onClick={() => setShowSources(value => !value)}><Icon name="book" size={15} />{sources.length} retrieved passages</button></div></>}
                </div>
              </section>
            )}
            <form className="composer-section" onSubmit={ask}>
              <div className="document-picker"><label htmlFor="document-scope">Search in</label><select id="document-scope" value={selectedDocument} onChange={event => changeDocumentScope(event.target.value)} disabled={pending}><option value="">All permitted documents</option>{library.map(item => <option key={item.document_id} value={item.document_id}>{item.title} · v{item.version}</option>)}</select></div>
              {libraryError && <p className="notice">{libraryError}</p>}
              <p className="library-summary">{library.length} documents available to your account. Choose one to ask about its exact terms.</p>
              <div className="conversation-status"><Icon name="chat" size={14} /><span>{conversationId ? 'Continuing this conversation' : 'New conversation'}</span>{conversationId && <button type="button" onClick={freshConversation} disabled={pending}>Start fresh</button>}</div>
              {conversationNotice && <p className="notice conversation-notice" role="status">{conversationNotice}</p>}
              {viewingEarlierAnswer && conversationId && <p className="notice conversation-notice">You’re viewing an earlier answer. Follow-ups continue from the latest question in this conversation.</p>}
              <div className={'composer ' + (pending ? 'is-loading' : '')}>
                <label htmlFor="question" className="sr-only">Ask a question about your documents</label>
                <textarea id="question" ref={textarea} value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); ask(event) } }} placeholder={conversationId ? 'Ask a follow-up, or explore another detail…' : 'Ask a complete question to begin…'} maxLength={2000} rows={2} disabled={pending} />
                <div className="composer-bottom"><span><Icon name="book" size={14} />Company documents</span><div className="composer-tools"><span className="character-count">{question.length > 0 ? question.length + ' / 2,000' : 'Enter to ask'}</span><button className="send-button" disabled={pending || !question.trim()} aria-label={pending ? 'Preparing answer' : 'Send question'}>{pending ? <span className="spinner" /> : <Icon name="arrow" size={20} />}</button></div></div>
              </div>
              <p className="composer-note">Follow-ups can refer to recent questions in this conversation. Every answer searches your permitted documents. New conversation clears the current topic.</p>
            </form>
            <footer className="workspace-footer"><span>KNOWLEDGE, WITH A SOURCE.</span><span>Check contract interpretations with HR.</span></footer>
          </main>

          {showSources && <aside className="evidence-panel" aria-label="Retrieved source passages">
            <div className="evidence-header"><span><Icon name="book" size={18} />Sources & context</span><button className="icon-button" onClick={() => setShowSources(false)} aria-label="Hide source panel"><Icon name="close" size={16} /></button></div>
            {sources.length > 0 ? <><div className="evidence-intro"><span className="eyebrow">BEHIND THIS ANSWER</span><p>{sources.length} passages retrieved for your question. A citation points to the matching passage below.</p></div><div className="source-list">{sources.map(source => <article className={'source-card ' + (sourceId === source.reference ? 'expanded' : '')} key={source.reference} id={'source-' + source.reference}><button className="source-toggle" aria-expanded={sourceId === source.reference} onClick={() => setSourceId(value => value === source.reference ? null : source.reference)}><span className="source-number">{source.reference}</span><span className="source-name">{source.source}<small>{[source.title, source.version && 'v' + source.version, source.page && 'Page ' + source.page].filter(Boolean).join(' · ') || 'Retrieved passage'}</small></span><Icon name="chevron" size={14} /></button>{sourceId === source.reference && <p className="source-text">{source.text}</p>}</article>)}</div><p className="evidence-disclaimer">Retrieved passages are reference material. They may not all support the answer.</p></> : <div className="source-empty"><div className="document-illustration"><Icon name="file" size={35} /><span /><span /></div><h2>See where it comes from.</h2><p>When you ask a question, the passages used as context appear here.</p><div className="how-it-works"><div><span>01</span><p>Search your documents</p></div><div><span>02</span><p>Read the relevant passages</p></div><div><span>03</span><p>Get an answer with references</p></div></div><div className="source-tip"><span className="tiny-square" /><p>No evidence? CompanyMind is instructed to say when it can’t find an answer.</p></div></div>}
          </aside>}
        </div>
      </div>
    </div>
  )
}



export default function App() {
  // Session and question history live only in React memory, never localStorage.
  const [session, setSession] = useState(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function signIn(event) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }), signal: AbortSignal.timeout(15000),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your sign-in details.')
      if (!data.access_token || !data.user) throw new Error('Unexpected sign-in response.')
      setPassword('')
      setSession(data)
    } catch (failure) {
      setError(failure instanceof TypeError ? 'Cannot reach CompanyMind. Check that the backend is running.' : failure.message)
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    const token = session?.access_token
    setSession(null) // Unmounts the workspace and discards the previous user's history.
    setPassword('')
    if (token) {
      try { await fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(5000) }) }
      catch { setError('Signed out locally. The server session will expire within an hour.') }
    }
  }

  if (session) return <KnowledgeWorkspace key={session.access_token} session={session} onSignOut={signOut} />
  return <main className="sign-in-page"><section className="sign-in-card">
    <div className="eyebrow">COMPANYMIND · EMPLOYEE KNOWLEDGE</div>
    <h1>Your workplace,<br /><em>with the details.</em></h1>
    <p>Sign in to explore your company, products, workplace policies, and the employment documents assigned to you.</p>
    <form onSubmit={signIn}>
      <label htmlFor="username">Username</label>
      <input id="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} maxLength={100} required />
      <label htmlFor="password">Password</label>
      <input id="password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} maxLength={128} required />
      {error && <p className="sign-in-error" role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}<Icon name="chevron" size={17} /></button>
    </form>
    <small>Your administrator creates your account. Signing out clears this tab’s question history. Relevant passages are sent to the configured AI provider.</small>
  </section></main>
}
