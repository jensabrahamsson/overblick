/**
 * Oracle page interactivity.
 * External file to comply with CSP (script-src 'self').
 */

async function askOracle() {
    const topicId = document.getElementById('oracle-topic-select').value;
    const question = document.getElementById('oracle-question').value;
    const btn = document.getElementById('oracle-ask-btn');
    const responseDiv = document.getElementById('oracle-response');

    if (!topicId && !question) {
        alert('Select a topic or enter a question');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Thinking...';
    responseDiv.className = 'oracle-response visible';
    responseDiv.textContent = 'Oracle is analyzing... (this may take 30-60 seconds)';

    try {
        const csrfMeta = document.body.getAttribute('hx-headers');
        let csrfToken = '';
        if (csrfMeta) {
            try { csrfToken = JSON.parse(csrfMeta)['X-CSRF-Token'] || ''; } catch(e) {}
        }

        const resp = await fetch('/api/oracle/ask', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify({ topic_id: topicId, question: question }),
        });
        const data = await resp.json();

        if (data.error) {
            responseDiv.textContent = 'Error: ' + data.error;
            responseDiv.style.color = 'var(--negative-color)';
        } else {
            // Render analysis + sources
            let text = data.analysis || 'No analysis returned.';
            text += '\n\n---\n';
            text += 'Topic: ' + (data.topic_name || topicId);
            text += '  |  Articles analyzed: ' + (data.article_count || 0);
            text += '  |  ' + new Date(data.timestamp).toLocaleString();

            if (data.sources && data.sources.length > 0) {
                text += '\n\nSOURCES:\n';
                // Deduplicate by source name
                const sourceCounts = {};
                data.sources.forEach(function(s) {
                    const name = s.source || 'Unknown';
                    sourceCounts[name] = (sourceCounts[name] || 0) + 1;
                });
                Object.entries(sourceCounts)
                    .sort(function(a, b) { return b[1] - a[1]; })
                    .forEach(function(entry) {
                        text += '  ' + entry[0] + ' (' + entry[1] + ' articles)\n';
                    });
                text += '\nARTICLES:\n';
                data.sources.slice(0, 15).forEach(function(s) {
                    text += '  - ' + s.title + (s.source ? ' [' + s.source + ']' : '') + '\n';
                });
                if (data.sources.length > 15) {
                    text += '  ... and ' + (data.sources.length - 15) + ' more\n';
                }
            }

            responseDiv.textContent = text;
            responseDiv.style.color = '';
        }
    } catch (e) {
        responseDiv.textContent = 'Request failed: ' + e.message;
        responseDiv.style.color = 'var(--negative-color)';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Ask Oracle';
    }
}

// Click topic card to select it in dropdown. Supports the legacy full-page
// Oracle cards and the external PolyTrader partial cards.
document.addEventListener('click', function (e) {
    const card = e.target.closest('.oracle-topic-card, .oracle-topic');
    if (!card) return;

    const tid = card.dataset.topicId;
    const select = document.getElementById('oracle-topic-select');
    if (select) {
        select.value = tid;
        document.querySelectorAll('.oracle-topic-card, .oracle-topic').forEach(function (c) {
            c.classList.remove('selected');
        });
        card.classList.add('selected');

        const askPanel = document.querySelector('.oracle-ask-panel') || document.querySelector('.oracle-ask');
        if (askPanel) askPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
});

let oracleCurrentCategory = 'all';

function initOracleFilters() {
    const searchInput = document.getElementById('oracleSearch');
    const cards = document.querySelectorAll('.oracle-topic-card, .oracle-topic');
    if (!cards.length) return;

    function updateVisibility() {
        const query = searchInput ? searchInput.value.toLowerCase() : '';
        cards.forEach(function (card) {
            const titleEl = card.querySelector('.oracle-card-title, .oracle-topic-name');
            const title = titleEl ? titleEl.textContent.toLowerCase() : '';
            const cat = card.dataset.category;
            const matchesSearch = !query || title.indexOf(query) !== -1;
            const matchesCat = oracleCurrentCategory === 'all' || cat === oracleCurrentCategory;
            card.style.display = matchesSearch && matchesCat ? '' : 'none';
        });
    }

    if (searchInput && searchInput.dataset.oracleFilterBound !== '1') {
        searchInput.dataset.oracleFilterBound = '1';
        searchInput.addEventListener('input', updateVisibility);
    }

    document.querySelectorAll('.pm-tab[data-cat], .oracle-filter-btn').forEach(function (btn) {
        if (btn.dataset.oracleFilterBound === '1') return;
        btn.dataset.oracleFilterBound = '1';
        btn.addEventListener('click', function () {
            const cat = btn.dataset.cat || 'all';
            document.querySelectorAll('.pm-tab[data-cat], .oracle-filter-btn').forEach(function (b) {
                b.classList.remove('active');
            });
            btn.classList.add('active');
            oracleCurrentCategory = cat;
            updateVisibility();
        });
    });
    updateVisibility();
}

function initOracleListeners() {
    const askBtn = document.getElementById('oracle-ask-btn');
    if (askBtn) {
        askBtn.removeEventListener('click', askOracle);
        askBtn.addEventListener('click', askOracle);
    }
    initOracleFilters();
}

document.addEventListener('DOMContentLoaded', initOracleListeners);

document.addEventListener('htmx:afterSwap', function (evt) {
    const target = evt.detail && evt.detail.target;
    if (target && (target.id === 'pm-content' || target.id === 'oracle-container')) {
        initOracleListeners();
    }
});
