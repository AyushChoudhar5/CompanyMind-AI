import { useEffect, useRef, useState } from 'react'

const audienceName = (visibility, isHR) => visibility === 'hr' ? 'HR only'
  : visibility === 'employee' ? (isHR ? 'Employee + HR' : 'You + HR') : 'Everyone'

export default function DocumentLibrary({ session, onBack, onAsk, onSignOut }) {
  const isHR = session.user.role === 'hr_admin'
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [audience, setAudience] = useState('all')
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [opening, setOpening] = useState(null)
  const previewRequest = useRef(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15000)
    fetch('/api/documents', { headers: { Authorization: 'Bearer ' + session.access_token }, signal: controller.signal })
      .then(async response => {
        if (!active || controller.signal.aborted) return
        if (response.status === 401) { onSignOut(); return }
        const body = await response.json()
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The library could not load.')
        if (!active || controller.signal.aborted) return
        setDocuments(body.documents)
        setError('')
      }).catch(failure => {
        if (!active) return
        setError(failure.name === 'AbortError' ? 'The library took too long to load. Go back and try again.' : failure.message)
      }).finally(() => { clearTimeout(timeout); if (active) setLoading(false) })
    return () => { active = false; clearTimeout(timeout); controller.abort(); previewRequest.current?.abort(); previewRequest.current = null }
  }, [session, onSignOut])

  async function openPreview(document) {
    previewRequest.current?.abort()
    const controller = new AbortController()
    previewRequest.current = controller
    const timeout = setTimeout(() => controller.abort(), 15000)
    setOpening(document.document_id)
    setPreview(null)
    setPreviewError('')
    try {
      const response = await fetch('/api/documents/' + encodeURIComponent(document.document_id) + '/preview', {
        headers: { Authorization: 'Bearer ' + session.access_token }, signal: controller.signal,
      })
      if (response.status === 401) { onSignOut(); return }
      const body = await response.json()
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'This document is unavailable.')
      if (previewRequest.current === controller && !controller.signal.aborted) setPreview(body)
    } catch (failure) {
      if (previewRequest.current === controller) setPreviewError(failure.name === 'AbortError'
        ? 'The preview took too long. Try again.' : failure.message)
    } finally {
      clearTimeout(timeout)
      if (previewRequest.current === controller) { setOpening(null); previewRequest.current = null }
    }
  }

  const counts = {
    all: documents.length,
    company: documents.filter(item => item.visibility === 'company').length,
    employee: documents.filter(item => item.visibility === 'employee').length,
    hr: documents.filter(item => item.visibility === 'hr').length,
  }
  const shown = documents.filter(item => (audience === 'all' || item.visibility === audience)
    && [item.title, item.source, item.category].some(value => (value || '').toLowerCase().includes(query.toLowerCase().trim())))

  return <main className="document-admin employee-library">
    <header className="admin-topbar">
      <button className="admin-back" onClick={onBack}>← Back to questions</button>
      <span>{session.user.username} <span className="admin-role">{isHR ? 'HR administrator' : 'Employee'} · {session.user.user_id}</span></span>
      <button className="admin-signout" onClick={onSignOut}>Sign out</button>
    </header>
    <div className="admin-content">
      <div className="eyebrow">COMPANYMIND · YOUR DOCUMENTS</div>
      <h1>{isHR ? 'The company library.' : 'Your work, documented.'}</h1>
      <p className="admin-description">{isHR
        ? 'Browse shared policies, employee documents and confidential HR material.'
        : 'Shared company policies and the private documents assigned to your account. Other employees’ documents and HR-only material are excluded.'}</p>
      <div className="library-toolbar">
        <div className="library-control"><label htmlFor="library-search">Find a document</label><input id="library-search" type="search" placeholder="Search title, filename or category" value={query} onChange={event => setQuery(event.target.value)} maxLength={200} /></div>
        <div className="library-control"><label htmlFor="library-audience">Show</label><select id="library-audience" value={audience} onChange={event => setAudience(event.target.value)}>
          <option value="all">All available ({counts.all})</option>
          <option value="employee">{isHR ? 'Employee documents' : 'My private documents'} ({counts.employee})</option>
          <option value="company">Shared policies ({counts.company})</option>
          {isHR && <option value="hr">HR only ({counts.hr})</option>}
        </select></div>
      </div>
      {error && <p className="admin-error" role="alert">{error}</p>}
      <div className="library-columns">
        <section className="admin-library" aria-label="Available documents">
          <h2>{shown.length} {shown.length === 1 ? 'document' : 'documents'}</h2>
          {loading ? <p className="admin-loading">Loading your documents…</p>
            : shown.length === 0 ? <p className="admin-loading">No documents match this view. HR can assign documents to your account.</p>
              : shown.map(document => <article className="admin-document" key={document.document_id}>
                <div className="admin-document-heading"><h3>{document.title}</h3><span className={'audience-badge ' + (document.visibility !== 'company' ? 'private' : '')}>{audienceName(document.visibility, isHR)}</span></div>
                <p>{document.source} · v{document.version} · {(document.category || '').replaceAll('_', ' ')}</p>
                <div className="library-actions">
                  <button onClick={() => openPreview(document)} disabled={opening === document.document_id}>{opening === document.document_id ? 'Opening…' : 'Preview passages'}</button>
                  <button onClick={() => onAsk(document)}>Ask about this document</button>
                </div>
              </article>)}
        </section>
        <section className="library-preview" aria-label="Document preview" aria-live="polite">
          {previewError && <p className="admin-error" role="alert">{previewError}</p>}
          {opening ? <p>Loading permitted passages…</p> : preview ? <>
            <div className="eyebrow">INDEXED DOCUMENT PREVIEW</div>
            <h2>{preview.document.title}</h2>
            <p className="preview-meta">{preview.document.source} · v{preview.document.version} · {audienceName(preview.document.visibility, isHR)}</p>
            <button className="preview-ask" onClick={() => onAsk(preview.document)}>Ask about this document</button>
            <p className="preview-note">These are the passages indexed for search, not a formatted copy of the original file. Adjacent passages may overlap.</p>
            {preview.truncated && <p className="preview-note">This preview is limited to 20 passages. Ask a specific question to search the document.</p>}
            {preview.passages.map((passage, index) => <article className="preview-passage" key={index}>
              <span>{passage.page ? 'Page ' + passage.page + ' · ' : ''}Passage {index + 1}</span><p>{passage.text}</p>
            </article>)}
          </> : !previewError && <><div className="eyebrow">READ BEFORE YOU ASK</div><h2>A source you can check.</h2><p>Choose Preview passages beside a document, or ask a question specifically about it.</p></>}
        </section>
      </div>
    </div>
  </main>
}
