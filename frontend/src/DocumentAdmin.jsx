import PolicyRevisions from './PolicyRevisions'
import { useEffect, useState } from 'react'

export default function DocumentAdmin({ session, onBack, onSignOut }) {
  const [revisionDocument, setRevisionDocument] = useState(null)
  const [libraryStatus, setLibraryStatus] = useState('all')
  const [documents, setDocuments] = useState([])
  const [employees, setEmployees] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('policy')
  const [version, setVersion] = useState('1.0')
  const [visibility, setVisibility] = useState('employee')
  const [owner, setOwner] = useState('')
  const [file, setFile] = useState(null)
  const [fileKey, setFileKey] = useState(0)
  const [deleteId, setDeleteId] = useState(null)
  const [librarySearch, setLibrarySearch] = useState('')
  const [libraryAudience, setLibraryAudience] = useState('all')

  async function request(path, options = {}) {
    const response = await fetch('/api' + path, {
      ...options,
      headers: { ...options.headers, Authorization: 'Bearer ' + session.access_token },
      signal: options.signal || AbortSignal.timeout(180000),
    })
    if (response.status === 401) { onSignOut(); throw new Error('Your session expired. Please sign in again.') }
    const body = await response.json()
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the file and document details, then try again.')
    return body
  }

  async function refresh(signal) {
    const [library, people] = await Promise.all([
      request('/admin/documents', { signal }), request('/admin/employees', { signal }),
    ])
    setDocuments(library.documents)
    setEmployees(people.employees)
  }

  useEffect(() => {
    const controller = new AbortController()
    refresh(controller.signal).catch(failure => {
      if (failure.name !== 'AbortError') setError('Could not load document management. ' + failure.message)
    }).finally(() => setLoading(false))
    return () => controller.abort()
    // Identity changes unmount this page; refresh is intentionally run on entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function upload(event) {
    event.preventDefault()
    if (busy) return
    setError('')
    setNotice('')
    if (!file) { setError('Choose a TXT or PDF document.'); return }
    if (!/\.(txt|pdf)$/i.test(file.name)) { setError('This version accepts TXT and text-based PDF files.'); return }
    if (file.size > 20 * 1024 * 1024) { setError('Choose a document no larger than 20 MiB.'); return }
    if (visibility === 'employee' && !owner) { setError('Choose the employee who owns this document.'); return }
    const form = new FormData()
    form.append('file', file)
    form.append('metadata', JSON.stringify({
      title, category, version, visibility, owner_id: visibility === 'employee' ? owner : null,
    }))
    setBusy(true)
    let indexed = false
    try {
      const result = await request('/admin/documents', { method: 'POST', body: form })
      indexed = true
      setNotice('“' + result.title + '” is indexed and available to its assigned audience.')
      setTitle('')
      setFile(null)
      setFileKey(value => value + 1)
      await refresh()
    } catch (failure) {
      setError(failure instanceof TypeError ? 'Could not reach the backend. Check your connection.'
        : failure.name === 'TimeoutError' || failure.name === 'AbortError'
          ? 'The request timed out. Refresh the list before retrying; indexing may still finish on the server.'
          : (indexed ? 'The upload succeeded, but the list could not refresh. ' : '') + failure.message)
    } finally { setBusy(false) }
  }

  async function remove(document) {
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await request('/admin/documents/' + encodeURIComponent(document.document_id), { method: 'DELETE' })
      setDeleteId(null)
      setNotice('“' + document.title + '” was removed from search.' + (result.cleanup_pending ? ' Index cleanup is pending; contact the administrator.' : ''))
      await refresh()
    } catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }

  const shownDocuments = documents.filter(document => (libraryAudience === 'all' || document.visibility === libraryAudience)
    && (libraryStatus === 'all' || (document.status || 'published') === libraryStatus)
    && [document.title, document.source, document.owner_username, document.owner_id, document.category]
      .some(value => (value || '').toLowerCase().includes(librarySearch.toLowerCase().trim())))
  const selectedOwner = employees.find(employee => employee.user_id === owner)
  if (revisionDocument) return <PolicyRevisions session={session} document={revisionDocument} onSignOut={onSignOut} onBack={() => { setRevisionDocument(null); refresh().catch(failure => setError(failure.message)) }} />
  return <main className="document-admin">
    <header className="admin-topbar">
      <button className="admin-back" onClick={onBack} disabled={busy}>← Back to questions</button>
      <span>{session.user.username} <span className="admin-role">HR administrator</span></span>
      <button className="admin-signout" onClick={onSignOut} disabled={busy}>Sign out</button>
    </header>
    <div className="admin-content">
      <div className="eyebrow">COMPANYMIND · KNOWLEDGE LIBRARY</div>
      <h1>Put the right information<br /><em>in the right hands.</em></h1>
      <p className="admin-description">Publish shared policies, assign private employee documents, or keep confidential material restricted to HR. The selected audience controls who can find it and ask questions.</p>
      {error && <div className="admin-error" role="alert">{error}</div>}
      {notice && <div className="admin-notice" role="status">{notice}</div>}
      <div className="admin-grid">
        <section className="upload-panel" aria-labelledby="upload-heading">
          <h2 id="upload-heading">Add a document</h2>
          <p>TXT or text-based PDF · up to 20 MiB</p>
          <form className="upload-form" onSubmit={upload}>
            <label htmlFor="upload-title">Document title</label>
            <input id="upload-title" value={title} onChange={event => setTitle(event.target.value)} placeholder="e.g. Remote working policy" maxLength={200} required disabled={busy} />
            <div className="upload-row"><div><label htmlFor="upload-category">Category</label><select id="upload-category" value={category} onChange={event => setCategory(event.target.value)} disabled={busy}>
              <option value="policy">Company policy</option><option value="offer_letter">Offer letter</option><option value="training_agreement">Training agreement</option><option value="it">IT guide</option><option value="benefits">Benefits</option><option value="other">Other</option>
            </select></div><div><label htmlFor="upload-version">Version label</label><input id="upload-version" value={version} onChange={event => setVersion(event.target.value)} maxLength={30} required disabled={busy} /></div></div>
            <fieldset disabled={busy}><legend>Who may use this document?</legend>
              <label className="audience-option"><input type="radio" name="visibility" value="employee" checked={visibility === 'employee'} onChange={() => setVisibility('employee')} /><span>One employee + HR<small>For personal employment documents.</small></span></label>
              <label className="audience-option"><input type="radio" name="visibility" value="company" checked={visibility === 'company'} onChange={() => setVisibility('company')} /><span>All employees<small>For policies everyone may read.</small></span></label>
              <label className="audience-option"><input type="radio" name="visibility" value="hr" checked={visibility === 'hr'} onChange={() => setVisibility('hr')} /><span>HR only<small>Confidential material employees must not see.</small></span></label>
            </fieldset>
            {visibility === 'employee' && <><label htmlFor="upload-owner">Document owner</label><select id="upload-owner" value={owner} onChange={event => setOwner(event.target.value)} required disabled={busy || loading}><option value="">Choose an employee</option>{employees.map(employee => <option key={employee.user_id} value={employee.user_id}>{employee.username} · {employee.user_id}</option>)}</select></>}
            <label htmlFor="upload-file">File</label><input key={fileKey} id="upload-file" type="file" accept=".txt,.pdf" onChange={event => setFile(event.target.files[0] || null)} required disabled={busy} />
            <div className="audience-summary">Available to: <strong>{visibility === 'hr' ? 'HR administrators only' : visibility === 'company' ? 'all employees' : selectedOwner ? selectedOwner.username + ' and HR' : 'select an employee'}</strong></div>
            <button className="upload-submit" type="submit" disabled={busy || loading}>{busy ? 'Working on your document…' : 'Upload and index document'}</button>
            <small>The document becomes searchable after indexing succeeds. Scanned PDFs require OCR and are not supported here yet.</small>
          </form>
        </section>
        <section className="admin-library" aria-labelledby="library-heading">
          <div className="admin-library-heading"><h2 id="library-heading">Registered documents <span>{documents.length}</span></h2><button disabled={busy || loading} onClick={() => { setLoading(true); refresh().catch(failure => setError(failure.message)).finally(() => setLoading(false)) }}>Refresh</button></div>
          <p>Use Review policy revision to replace a policy with an approved version. Drafts remain hidden from employee search.</p>
          <div className="library-toolbar admin-filters">
            <div className="library-control"><label htmlFor="admin-library-search">Find a document</label><input id="admin-library-search" type="search" placeholder="Title, employee, ID or category" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)} maxLength={200} /></div>
            <div className="library-control"><label htmlFor="admin-library-audience">Audience</label><select id="admin-library-audience" value={libraryAudience} onChange={event => setLibraryAudience(event.target.value)}><option value="all">All audiences</option><option value="company">All employees</option><option value="employee">One employee + HR</option><option value="hr">HR only</option></select></div>
            <div className="library-control"><label htmlFor="admin-status">Publication status</label><select id="admin-status" value={libraryStatus} onChange={event => setLibraryStatus(event.target.value)}>{['all', 'published', 'draft', 'scheduled', 'archived'].map(value => <option key={value} value={value}>{value === 'all' ? 'All versions' : value}</option>)}</select></div>
          </div>
          {loading ? <p className="admin-loading">Loading your library…</p> : shownDocuments.length === 0 ? <p className="admin-loading">No documents match this view.</p> : <div className="admin-document-list">{shownDocuments.map(document => <article className="admin-document" key={document.document_id}>
            <div className="admin-document-heading"><h3>{document.title}</h3><span className={'audience-badge ' + (document.visibility !== 'company' ? 'private' : '')}>{document.visibility === 'hr' ? 'HR only' : document.visibility === 'company' ? 'Shared' : 'Private'}</span></div>
            <p>{document.source} · v{document.version}</p>
            <div className="admin-document-details"><span>{document.visibility === 'hr' ? 'HR administrators only' : document.visibility === 'company' ? 'All employees' : (document.owner_username || document.owner_id) + ' + HR'}</span><span>{document.chunks > 0 ? 'Indexed' : 'Not indexed'}</span></div>
            <div className="revision-document-actions"><span className={'status-pill ' + document.status}>{document.status || 'published'}</span><button className="revision-link" onClick={() => setRevisionDocument({ ...document, review_existing: ['draft', 'scheduled', 'archived'].includes(document.status) })} disabled={busy}>{document.status === 'draft' ? 'Review draft' : document.status === 'scheduled' ? 'View scheduled revision' : document.status === 'archived' ? 'Version history' : 'Review policy revision'}</button></div>
            {deleteId === document.document_id ? <div className="delete-confirm"><p>Remove this document from the library and search? Its source file is kept locally for recovery.</p><button className="confirm-remove" onClick={() => remove(document)} disabled={busy}>Remove from search</button><button onClick={() => setDeleteId(null)} disabled={busy}>Cancel</button></div>
              : <button className="remove-document" onClick={() => setDeleteId(document.document_id)} disabled={busy}>Remove…</button>}
          </article>)}</div>}
        </section>
      </div>
    </div>
  </main>
}
