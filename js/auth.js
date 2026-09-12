  // ---- No login in this build ---------------------------------------------------
  // The workspace opens straight into case-taking: no account screen, no sign-in.
  // A Supabase session is still used if this browser already holds one, because
  // the database only accepts its writes from a signed-in user.
  (function startWorkspace(){
    function enter(){
      window.__appEntered = true;
      // the entry language chooser (and anything else waiting for the workspace
      // to be ready) listens for this, so fire it the way a sign-in would
      window.dispatchEvent(new Event('appenter'));
    }
    if(document.readyState === 'loading'){
      document.addEventListener('DOMContentLoaded', enter);
    } else {
      enter();
    }
  })();
