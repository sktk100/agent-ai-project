"""Firebase-enabled HEALTH QUEST deployment entrypoint.

Deploy this file as a separate Streamlit Community Cloud app and configure
``firebase.enabled = true`` only in that app's Secrets.  The existing
``app_integrated.py`` deployment can therefore remain independent.
"""

from integrated_app_runtime import run_integrated_app


# This distinct entrypoint gives Community Cloud a separate deployment while
# both integrated apps reuse exactly the same tested runtime.
run_integrated_app()
