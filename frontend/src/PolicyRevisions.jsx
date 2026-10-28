import { useCallback, useEffect, useState } from 'react'

const dateLabel = value => value ? new Date(value * 1000).toLocaleString() : 'Immediately'
const statusLabel = value => ({ changed: 'Changed', unchanged: 'Unchanged', needs_review: 'Needs review', running: 'Review in progress', complete: 'Review complete', failed: 'Review interrupted' }[value] || value)

export default function PolicyRevisions({ session, document, onBack, onSignOut }) {
  const baseId = document.review_existing && document.replaces_id ? document.replaces_id : document.document_id
  const [base, setBase] = useState(null)
  const [versions, setVersions] = useState([])
  const [revisionId, setRevisionId] = useState(document.review_existing && document.replaces_id ? document.document_id : '')
  const [detail, setDetail] = useState(null)
  const [version, setVersion] = useState('')
  const [file, setFile] = useState(null)
  const [fileKey, setFileKey] = useState(0)
  const [questions, setQuestions] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [approved, setApproved] = useState(false)
  const [timing, setTiming] = useState('now')
  const [effectiveAt, setEffectiveAt] = useState('')

  const request = useCallback(async (path, options = {}) => {
    const response = await fetch('/api' + path, {
      ...options, headers: { ...options.headers, Authorization: 'Bearer ' + session.access_token },
      signal: options.signal || AbortSignal.timeout(180000),
    })
    if (response.status === 401) { onSignOut(); throw new Error('Sign in again to review policies.') }
    const body = await response.json()
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The revision request could not be completed.')
    return body
  }, [session.access_token, onSignOut])

  useEffect(() => {
    const controller = new AbortController()
    request('/admin/documents/' + encodeURIComponent(baseId) + '/revisions', { signal: controller.signal })
      .then(body => {
        if (controller.signal.aborted) return
        setBase(body.base)
        setVersions(body.revisions)
        setLoading(false)
      }).catch(failure => { if (!controller.signal.aborted) { setError(failure.message); setLoading(false) } })
    return () => controller.abort()
  }, [baseId, request, refresh])

  useEffect(() => {
    if (!revisionId) return
    const controller = new AbortController()
    let timeout
    let first = true
    async function poll() {
      try {
        const body = await request('/admin/revisions/' + encodeURIComponent(revisionId), { signal: controller.signal })
        if (controller.signal.aborted) return
        setDetail(body)
        if (first) {
          setQuestions((body.review?.questions || body.suggested_questions).join('\n'))
          setApproved(false)
          first = false
        }
        if (body.review?.status === 'running') timeout = setTimeout(poll, 2000)
      } catch (failure) { if (!controller.signal.aborted) setError(failure.message) }
    }
    poll()
    return () => { controller.abort(); clearTimeout(timeout) }
  }, [revisionId, request, refresh])

  function selectRevision(id) {
    setRevisionId(id); setDetail(null); setError(''); setNotice(''); setApproved(false)
  }

  async function upload(event) {
    event.preventDefault()
    if (busy) return
    if (!file || !/\.(txt|pdf)$/i.test(file.name) || file.size > 20 * 1024 * 1024) {
      setError('Choose a TXT or text-based PDF no larger than 20 MiB.'); return
    }
    setBusy(true); setError(''); setNotice('')
    try {
      const form = new FormData()
      form.append('metadata', JSON.stringify({ version: version.trim() }))
      form.append('file', file)
      const result = await request('/admin/documents/' + encodeURIComponent(baseId) + '/revisions', { method: 'POST', body: form })
      selectRevision(result.document_id)
      setVersion(''); setFile(null); setFileKey(value => value + 1)
      setRefresh(value => value + 1)
      setNotice('Draft uploaded. Employees continue to use the current policy until you publish the revision.')
    } catch (failure) { setError(failure.name === 'TimeoutError' ? 'Upload timed out. Reopen version history before retrying; the draft may have been saved.' : failure.message) }
    finally { setBusy(false) }
  }

  async function review() {
    const list = questions.split('\n').map(value => value.trim()).filter(Boolean)
    if (list.length < 1 || list.length > 8 || list.some(value => value.length < 5 || value.length > 500)) {
      setError('Enter 1–8 questions, one per line, with 5–500 characters each.'); return
    }
    setBusy(true); setError(''); setNotice(''); setApproved(false)
    try {
      await request('/admin/revisions/' + encodeURIComponent(revisionId) + '/review', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ questions: list }),
      })
      setDetail(current => ({ ...current, review: { status: 'running', questions: list, results: [] } }))
      setRefresh(value => value + 1)
    } catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }

  async function publish() {
    if (!approved || busy) return
    let effective = null
    if (timing === 'scheduled') {
      const parsed = new Date(effectiveAt)
      if (!effectiveAt || Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) { setError('Choose a future effective date and time.'); return }
      effective = parsed.toISOString()
    }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await request('/admin/revisions/' + encodeURIComponent(revisionId) + '/publish', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_id: detail.review.review_id, acknowledge_review: approved, effective_at: effective }),
      })
      setNotice(result.revision.status === 'scheduled'
        ? 'Revision scheduled for ' + dateLabel(result.revision.effective_from) + '. The current version remains active until then.'
        : 'Revision published. Current-policy searches now use the new version; the previous version is archived.')
      setRefresh(value => value + 1)
    } catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }

  const report = detail?.review
  const running = report?.status === 'running'
  const results = report?.results || []
  const isDraft = detail?.revision.status === 'draft'
  const canUpload = base?.status === 'published' && base.effective_until === 4102444800
  const audience = base?.visibility === 'company' ? 'All employees' : base?.visibility === 'hr' ? 'HR only' : (base?.owner_id + ' + HR')

  return <main className="document-admin revision-page">
    <header className="admin-topbar"><button className="admin-back" onClick={onBack} disabled={busy}>← Back to document management</button><span>{session.user.username} <span className="admin-role">HR administrator</span></span><button className="admin-signout" onClick={onSignOut} disabled={busy}>Sign out</button></header>
    <div className="admin-content revision-content">
      <div className="eyebrow">POLICY GOVERNANCE</div><h1>Review policy changes</h1>
      <p className="admin-description">Compare answers before publishing a revised document. Employees keep the current policy until the approved revision takes effect.</p>
      <ol className="revision-steps"><li>1 · Upload draft</li><li>2 · Review answer impact</li><li>3 · Approve and publish</li></ol>
      {error && <div className="admin-error" role="alert">{error}</div>}
      {notice && <div className="admin-notice" role="status">{notice}</div>}
      {loading ? <p>Loading policy versions…</p> : <div className="revision-layout">
        <aside className="revision-sidebar">
          <section className="revision-panel"><h2>Baseline version</h2><h3>{base?.title}</h3><p>Version {base?.version} · <span className="status-pill">{base?.status}</span></p><p>{audience}</p><small>A revision inherits the same audience and employee owner.</small></section>
          <section className="revision-panel"><h2>Upload a draft revision</h2>
            {canUpload ? <form className="upload-form" onSubmit={upload}>
              <label htmlFor="revision-version">New version label</label><input id="revision-version" value={version} onChange={event => setVersion(event.target.value)} placeholder="e.g. 2.0" maxLength={30} required disabled={busy || running} />
              <label htmlFor="revision-file">Revised document</label><input key={fileKey} id="revision-file" type="file" accept=".txt,.pdf" onChange={event => setFile(event.target.files[0] || null)} required disabled={busy || running} />
              <button className="upload-submit" disabled={busy || running}>{busy ? 'Saving…' : 'Save draft revision'}</button><small>TXT or text-based PDF · up to 20 MiB. Drafts are excluded from normal search and chat.</small>
            </form> : <p>This version is archived or has a scheduled replacement. Return to the current policy to create another revision.</p>}
          </section>
          <section className="revision-panel"><label htmlFor="revision-select">Revision history</label><select id="revision-select" value={revisionId} onChange={event => selectRevision(event.target.value)} disabled={busy}>
            <option value="">Choose a revision</option>{versions.map(item => <option key={item.document_id} value={item.document_id}>v{item.version} · {item.status}</option>)}
          </select><small>Published versions and their review reports remain available here to HR.</small></section>
        </aside>
        <div className="revision-main">
          {!detail ? <section className="revision-panel revision-empty"><h2>{revisionId ? 'Loading revision…' : 'Start with a draft'}</h2><p>Upload a replacement document or select a saved revision. Then choose the employee questions you want to check.</p></section> : <>
            <section className="revision-panel"><div className="revision-heading"><h2>v{detail.base.version} → v{detail.revision.version}</h2><span className={'status-pill ' + detail.revision.status}>{detail.revision.status}</span></div>
              <details className="revision-diff"><summary>Inspect changes in indexed text</summary><p>Overlapping search passages can repeat text. This is a text comparison, not a formatted document redline.</p><pre>{detail.text_diff || 'No indexed text differences found.'}</pre>{detail.diff_truncated && <p>Large comparison: only the first 300 diff lines are shown.</p>}</details>
              {isDraft && <><label className="question-list-label" htmlFor="review-questions">Test questions — one per line</label><textarea id="review-questions" className="review-questions" rows={5} value={questions} onChange={event => setQuestions(event.target.value)} disabled={busy || running} maxLength={4008} />
                <p className="review-help">Check 1–8 questions per run. Each question compares retrieved evidence from both versions using your configured AI provider.</p>
                <button className="primary-button" onClick={review} disabled={busy || running}>{running ? 'Review running…' : report ? 'Run a new impact review' : 'Run impact review'}</button></>}
            </section>
            {report && <section className="revision-panel"><div className="revision-heading"><h2>Answer impact report</h2><span className={'status-pill ' + report.status}>{statusLabel(report.status)}</span></div>
              <p className="review-help">AI-assisted estimates for these questions only. Check the evidence before approval; this does not establish legal precedence or detect every possible conflict.</p>
              {running && <p className="review-progress" role="status">Checked {results.length} of {report.questions.length} questions. You may leave this screen and return; the review runs on the server.</p>}
              {report.error && <p className="admin-error" role="alert">{report.error}</p>}
              <div className="impact-stats">{['changed', 'unchanged', 'needs_review'].map(state => <div key={state}><strong>{results.filter(item => item.status === state).length}</strong><span>{statusLabel(state)}</span></div>)}</div>
              <div className="impact-results">{results.map((item, index) => <article className="impact-row" key={index}>
                <div className="revision-heading"><h3>{item.question}</h3><span className={'status-pill ' + item.status}>{statusLabel(item.status)}</span></div>
                <div className="answer-comparison"><div><span>CURRENT · v{detail.base.version}</span><p>{item.current.answer}</p><small>References: {item.current.references.length ? item.current.references.map(value => '[' + value + ']').join(' ') : 'No supporting passage'}</small></div><div><span>PROPOSED · v{detail.revision.version}</span><p>{item.proposed.answer}</p><small>References: {item.proposed.references.length ? item.proposed.references.map(value => '[' + value + ']').join(' ') : 'No supporting passage'}</small></div></div>
                <p className="impact-reason">{item.reason}</p>
                <details className="impact-evidence"><summary>Read supporting passages</summary><div className="answer-comparison">{[['Current evidence', item.current_sources], ['Proposed evidence', item.proposed_sources]].map(([label, sources]) => <div key={label}><h4>{label}</h4>{sources.map(source => <section key={source.reference}><small>[{source.reference}] {source.source}{source.page ? ' · Page ' + source.page : ''}</small><p>{source.text}</p></section>)}</div>)}</div></details>
              </article>)}</div>
            </section>}
            {isDraft && report?.status === 'complete' && <section className="revision-panel publish-panel"><h2>Approve publication</h2><p>Confirm the report and source passages. Publishing changes which version supplies answers; formal company approval and employee communication remain HR responsibilities.</p>
              {results.some(item => item.status === 'needs_review') && <p className="notice">Some questions need review. Resolve or explicitly accept these gaps before publication.</p>}
              <div className="publish-timing"><label htmlFor="publish-timing">When should it take effect?</label><select id="publish-timing" value={timing} onChange={event => setTiming(event.target.value)} disabled={busy}><option value="now">Publish now</option><option value="scheduled">Schedule an effective date</option></select>
                {timing === 'scheduled' && <><label htmlFor="effective-time">Effective date and time (your local time)</label><input id="effective-time" type="datetime-local" value={effectiveAt} onChange={event => setEffectiveAt(event.target.value)} disabled={busy} /></>}
              </div>
              <label className="publish-ack"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} disabled={busy} />I reviewed the answers and supporting passages and approve this revision.</label>
              <button className="primary-button" onClick={publish} disabled={!approved || busy}>{timing === 'scheduled' ? 'Approve scheduled revision' : 'Approve and publish revision'}</button>
            </section>}
            {detail.revision.status !== 'draft' && <section className="revision-panel"><h2>Publication record</h2><p>Effective from {dateLabel(detail.revision.effective_from)}. Approved by {detail.revision.published_by || 'the administrator'}.</p><p>The previous version stops supplying current answers at the same time.</p></section>}
          </>}
        </div>
      </div>}
    </div>
  </main>
}
