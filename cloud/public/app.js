// Status page: who is signed in and how many shares wait for them (counts only, no content).
(async () => {
  const state = document.getElementById('state');
  const err = document.getElementById('err');
  try {
    const who = await (await fetch('/api/whoami')).json();
    if (who.error) throw new Error(who.error);
    const inbox = await (await fetch('/api/inbox?count=1')).json();
    if (inbox.error) throw new Error(inbox.error);
    state.textContent = '';
    const b = document.createElement('span');
    b.id = 'who';
    b.textContent = who.name;
    state.append('Signed in as ', b, `. ${inbox.count} share${inbox.count === 1 ? '' : 's'} waiting for you.`);
  } catch (e) {
    state.textContent = 'Could not read the inbox.';
    err.textContent = e.message;
    err.hidden = false;
  }
})();
