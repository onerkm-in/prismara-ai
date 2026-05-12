import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Send, Terminal, Sparkles, Loader2, FolderOpen, CheckCircle2, AlertCircle, FileCode, ChevronDown, ChevronRight, ShieldCheck, Download, LogOut, Settings2, Activity, Copy as CopyIcon, RefreshCw, X as XIcon, History, Trash2 } from 'lucide-react';
import './App.css';

const EMPTY_SSO_DRAFT = {
  id: '',
  label: '',
  flow: 'client_credentials',
  token_url: '',
  device_auth_url: '',
  client_id: '',
  client_secret: '',
  scope: '',
};

const EMPTY_CUSTOM_MODEL_DRAFT = {
  id: '',
  label: '',
  base_url: '',
  model: '',
  tier: 'custom',
  capabilities: 'general,code,write',
  auth_type: 'api_key',
  api_key: '',
  sso_profile_id: '',
  extra_headers: '',
  notes: '',
};

const PROVIDER_ACCESS = [
  {
    id: 'openai',
    name: 'ChatGPT / OpenAI API',
    consumerUrl: 'https://chatgpt.com/',
    consoleUrl: 'https://platform.openai.com/api-keys',
    docsUrl: 'https://platform.openai.com/docs/api-reference/authentication',
    key: 'OPENAI_API_KEY',
    sso: 'API key',
    note: 'ChatGPT login is separate from OpenAI API access.',
  },
  {
    id: 'anthropic',
    name: 'Claude / Anthropic API',
    consumerUrl: 'https://claude.ai/',
    consoleUrl: 'https://console.anthropic.com/settings/keys',
    docsUrl: 'https://platform.claude.com/docs/en/api/overview',
    key: 'ANTHROPIC_API_KEY',
    sso: 'API key or WIF bearer',
    note: 'Set ANTHROPIC_SSO_PROFILE_ID for a bearer-token SSO profile.',
  },
  {
    id: 'gemini',
    name: 'Gemini / Google API',
    consumerUrl: 'https://gemini.google.com/app',
    consoleUrl: 'https://aistudio.google.com/app/apikey',
    docsUrl: 'https://ai.google.dev/gemini-api/docs/api-key',
    key: 'GOOGLE_API_KEY',
    sso: 'API key or OAuth',
    note: 'Set GOOGLE_SSO_PROFILE_ID and GOOGLE_CLOUD_PROJECT_ID for OAuth.',
  },
];

const SSO_TEMPLATES = [
  {
    name: 'Google Gemini OAuth',
    values: {
      id: 'google-gemini-oauth',
      label: 'Google Gemini OAuth',
      flow: 'device_code',
      token_url: 'https://oauth2.googleapis.com/token',
      device_auth_url: 'https://oauth2.googleapis.com/device/code',
      scope: 'https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/generative-language.retriever',
    },
  },
  {
    name: 'Azure OpenAI SSO',
    values: {
      id: 'azure-openai',
      label: 'Azure OpenAI',
      flow: 'client_credentials',
      token_url: 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
      scope: 'https://cognitiveservices.azure.com/.default',
    },
  },
];

const CUSTOM_MODEL_TEMPLATES = [
  {
    name: 'OpenAI compatible',
    values: {
      label: 'OpenAI Compatible',
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      tier: 'paid',
      auth_type: 'api_key',
      notes: 'OpenAI-compatible chat completions endpoint.',
    },
  },
  {
    name: 'Gemini compatible',
    values: {
      label: 'Gemini OpenAI Compatible',
      base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
      model: 'gemini-2.5-flash',
      tier: 'paid',
      auth_type: 'api_key',
      notes: 'Gemini API through Google OpenAI-compatible endpoint.',
    },
  },
  {
    name: 'Enterprise SSO gateway',
    values: {
      label: 'Enterprise LLM Gateway',
      base_url: 'https://your-gateway.example.com/v1',
      model: 'default',
      tier: 'custom',
      auth_type: 'sso_client_creds',
      notes: 'OpenAI-compatible gateway protected by an SSO profile.',
    },
  },
];

const isSensitiveCredentialKey = (key) => (
  /API_KEY|SECRET|TOKEN|PASSWORD|SERVICE_ACCOUNT_JSON/.test(key)
);

function App() {
  const [prompt, setPrompt] = useState('');
  const [result, setResult] = useState('');
  const [finalAnswer, setFinalAnswer] = useState('');
  const [runMetrics, setRunMetrics] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [streamEvents, setStreamEvents] = useState([]);
  const [activeAgent, setActiveAgent] = useState('');
  const [rawOutputOpen, setRawOutputOpen] = useState(false);
  const [pipelineStages, setPipelineStages] = useState({});
  const [pipelineStarted, setPipelineStarted] = useState(false);
  const [requestIntents, setRequestIntents] = useState([]);
  const [pipelineWarnings, setPipelineWarnings] = useState([]);
  const [onlineConsent, setOnlineConsent] = useState(false);
  const [consentUsages, setConsentUsages] = useState([]);
  const [setupRequired, setSetupRequired] = useState(null);
  const [onlineModeBanner, setOnlineModeBanner] = useState('');
  const [chatHistory, setChatHistory] = useState([]);
  const [chatHistoryLoading, setChatHistoryLoading] = useState(false);
  const [chatHistoryError, setChatHistoryError] = useState('');
  const [doctorOpen, setDoctorOpen] = useState(() => {
    try {
      return localStorage.getItem('prismara_system_doctor_seen') !== 'true';
    } catch {
      return true;
    }
  });
  const [trustOpen, setTrustOpen] = useState(false);
  const [systemDoctor, setSystemDoctor] = useState(null);
  const [systemDoctorLoading, setSystemDoctorLoading] = useState(false);
  const [systemDoctorError, setSystemDoctorError] = useState('');

  // Diagnostics modal
  const [diagOpen, setDiagOpen] = useState(false);
  const [traces, setTraces] = useState([]);
  const [tracesLoading, setTracesLoading] = useState(false);
  const [selectedTraceId, setSelectedTraceId] = useState(null);
  const [traceDetail, setTraceDetail] = useState(null);
  const [systemMetrics, setSystemMetrics] = useState(null);
  const [copyToast, setCopyToast] = useState('');

  // Workspace state
  const [folderPath, setFolderPath] = useState('');
  const [workspace, setWorkspace] = useState(null);  // { folder, file_count, file_tree }
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState('');
  const [fileTreeOpen, setFileTreeOpen] = useState(false);
  const [showUsageAgreement, setShowUsageAgreement] = useState(false);
  const [pendingFolderPath, setPendingFolderPath] = useState('');
  const [usageAgreementAccepted, setUsageAgreementAccepted] = useState(() => {
    try {
      return localStorage.getItem('prismara_ai_usage_agreement_accepted') === 'true';
    } catch {
      return false;
    }
  });

  // Admin control panel state
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminBootstrap, setAdminBootstrap] = useState({ google_oauth_client_id: '' });
  const [adminSession, setAdminSession] = useState({ authenticated: false, email: '' });
  const [adminError, setAdminError] = useState('');
  const [adminLoading, setAdminLoading] = useState(false);

  const [guardMode, setGuardMode] = useState('auto');
  const [transferMinutes, setTransferMinutes] = useState(1440);
  const [credentialsSet, setCredentialsSet] = useState({});
  const [keys, setKeys] = useState({
    OPENAI_API_KEY: '',
    OPENAI_ORG_ID: '',
    OPENAI_PROJECT_ID: '',
    ANTHROPIC_API_KEY: '',
    ANTHROPIC_SSO_PROFILE_ID: '',
    GOOGLE_API_KEY: '',
    GOOGLE_SSO_PROFILE_ID: '',
    GOOGLE_CLOUD_PROJECT_ID: '',
    OPENROUTER_API_KEY: '',
    GROQ_API_KEY: '',
    DEEPSEEK_API_KEY: '',
    COHERE_API_KEY: '',
    SARVAM_API_KEY: '',
    GCS_SERVICE_ACCOUNT_JSON: '',
    GOOGLE_OAUTH_CLIENT_ID: '',
  });
  const [ssoProfiles, setSsoProfiles] = useState([]);
  const [ssoStatus, setSsoStatus] = useState({});
  const [ssoDraft, setSsoDraft] = useState(EMPTY_SSO_DRAFT);
  const [ssoDeviceFlow, setSsoDeviceFlow] = useState(null);
  const [customModels, setCustomModels] = useState([]);
  const [customModelDraft, setCustomModelDraft] = useState(EMPTY_CUSTOM_MODEL_DRAFT);

  // Local AI (Ollama + custom local endpoint) admin state
  const [localAiStatus, setLocalAiStatus] = useState(null);
  const [localAiLoading, setLocalAiLoading] = useState(false);
  const [localAiInstallEvents, setLocalAiInstallEvents] = useState([]);
  const [localAiInstalling, setLocalAiInstalling] = useState(false);
  const [pullingModel, setPullingModel] = useState(null);
  const [pullProgress, setPullProgress] = useState(null);
  const [updatingModels, setUpdatingModels] = useState(false);
  const [updateProgress, setUpdateProgress] = useState(null);
  const [hardwarePolicy, setHardwarePolicy] = useState(null);
  const [customProbes, setCustomProbes] = useState([]);
  const [localAiError, setLocalAiError] = useState('');

  const [integrations, setIntegrations] = useState([]);
  const [integrationDraft, setIntegrationDraft] = useState({
    id: '',
    name: '',
    endpoint: '',
    auth_type: 'none',
    api_key: '',
    secret: '',
    notes: '',
  });
  const [localAuth, setLocalAuth] = useState({ username: '', password: '' });
  const [storageHealth, setStorageHealth] = useState(null);
  const [backupConfig, setBackupConfig] = useState({
    mode: 'local',
    gcs_bucket: '',
    gcs_prefix: 'prismara-backups',
    last_cloud_backup_at: 0,
    last_cloud_backup_bucket: '',
    last_cloud_backup_blob: '',
    last_gcs_test_at: 0,
    last_gcs_test_ok: null,
  });
  const [backupLastResult, setBackupLastResult] = useState(null);
  const [gcsTestResult, setGcsTestResult] = useState(null);

  const loadChatHistory = async ({ silent = false } = {}) => {
    if (!silent) setChatHistoryLoading(true);
    setChatHistoryError('');
    try {
      const res = await axios.get('/api/chat-history');
      setChatHistory(Array.isArray(res.data?.messages) ? res.data.messages : []);
    } catch (e) {
      setChatHistoryError(e.response?.data?.error || 'Could not load chat history');
    } finally {
      if (!silent) setChatHistoryLoading(false);
    }
  };

  const fetchSystemDoctor = async ({ silent = false } = {}) => {
    if (!silent) setSystemDoctorLoading(true);
    setSystemDoctorError('');
    try {
      const r = await axios.get('/api/system-doctor');
      const payload = r.data || null;
      setSystemDoctor(payload);
      if (payload?.local_ai) {
        setLocalAiStatus(payload.local_ai);
        if (payload.local_ai.hardware_policy) setHardwarePolicy(payload.local_ai.hardware_policy);
      }
    } catch (e) {
      setSystemDoctorError(e.response?.data?.error || 'Could not run System Doctor');
    } finally {
      if (!silent) setSystemDoctorLoading(false);
    }
  };

  const clearChatHistory = async () => {
    setChatHistoryError('');
    setChatHistoryLoading(true);
    try {
      await axios.delete('/api/chat-history');
      setChatHistory([]);
    } catch (e) {
      setChatHistoryError(e.response?.data?.error || 'Could not clear chat history');
    } finally {
      setChatHistoryLoading(false);
    }
  };

  const restoreChatTurn = (turn) => {
    const userText = turn?.user?.content || '';
    const assistantText = turn?.assistant?.content || '';
    setPrompt(userText);
    setFinalAnswer(assistantText);
    setResult(assistantText);
    setError('');
    setRawOutputOpen(false);
    setRunMetrics(null);
  };

  useEffect(() => {
    loadChatHistory();
    fetchSystemDoctor({ silent: true });
  }, []);

  useEffect(() => {
    if (!doctorOpen) return;
    try {
      localStorage.setItem('prismara_system_doctor_seen', 'true');
    } catch {
      // ignore localStorage failures
    }
  }, [doctorOpen]);

  useEffect(() => {
    if (!adminOpen) return;

    const loadAdmin = async () => {
      try {
        const [boot, sess, guard, hardware, creds, profiles, status, models, ints, health, bcfg] = await Promise.all([
          axios.get('/api/admin/bootstrap'),
          axios.get('/api/admin/session'),
          axios.get('/api/settings/data-guard').catch(() => ({ data: { mode: 'auto' } })),
          axios.get('/api/settings/hardware').catch(() => ({ data: null })),
          axios.get('/api/settings/credentials').catch(() => ({ data: {} })),
          axios.get('/api/sso/profiles').catch(() => ({ data: [] })),
          axios.get('/api/sso/status').catch(() => ({ data: {} })),
          axios.get('/api/custom-models').catch(() => ({ data: [] })),
          axios.get('/api/integrations').catch(() => ({ data: [] })),
          axios.get('/api/admin/storage-health').catch(() => ({ data: null })),
          axios.get('/api/admin/backup-config').catch(() => ({ data: { mode: 'local', gcs_bucket: '', gcs_prefix: 'prismara-backups', last_cloud_backup_at: 0, last_cloud_backup_bucket: '', last_cloud_backup_blob: '', last_gcs_test_at: 0, last_gcs_test_ok: null } })),
        ]);

        setAdminBootstrap(boot.data || {});
        setAdminSession(sess.data || { authenticated: false });
        setGuardMode(guard.data?.mode || 'auto');
        setHardwarePolicy(hardware.data || null);
        setSsoProfiles(Array.isArray(profiles.data) ? profiles.data : []);
        setSsoStatus(status.data || {});
        setCustomModels(Array.isArray(models.data) ? models.data : []);
        setIntegrations(Array.isArray(ints.data) ? ints.data : []);
        setStorageHealth(health.data || null);
        setBackupConfig(bcfg.data || { mode: 'local', gcs_bucket: '', gcs_prefix: 'prismara-backups', last_cloud_backup_at: 0, last_cloud_backup_bucket: '', last_cloud_backup_blob: '', last_gcs_test_at: 0, last_gcs_test_ok: null });

        const keysSet = creds.data?.keys_set || {};
        setCredentialsSet(keysSet);
        setKeys((prev) => ({
          ...prev,
          GOOGLE_OAUTH_CLIENT_ID: boot.data?.google_oauth_client_id || prev.GOOGLE_OAUTH_CLIENT_ID,
          OPENAI_API_KEY: keysSet.OPENAI_API_KEY ? prev.OPENAI_API_KEY : '',
          OPENAI_ORG_ID: keysSet.OPENAI_ORG_ID ? prev.OPENAI_ORG_ID : '',
          OPENAI_PROJECT_ID: keysSet.OPENAI_PROJECT_ID ? prev.OPENAI_PROJECT_ID : '',
          ANTHROPIC_API_KEY: keysSet.ANTHROPIC_API_KEY ? prev.ANTHROPIC_API_KEY : '',
          ANTHROPIC_SSO_PROFILE_ID: keysSet.ANTHROPIC_SSO_PROFILE_ID ? prev.ANTHROPIC_SSO_PROFILE_ID : '',
          GOOGLE_API_KEY: keysSet.GOOGLE_API_KEY ? prev.GOOGLE_API_KEY : '',
          GOOGLE_SSO_PROFILE_ID: keysSet.GOOGLE_SSO_PROFILE_ID ? prev.GOOGLE_SSO_PROFILE_ID : '',
          GOOGLE_CLOUD_PROJECT_ID: keysSet.GOOGLE_CLOUD_PROJECT_ID ? prev.GOOGLE_CLOUD_PROJECT_ID : '',
          OPENROUTER_API_KEY: keysSet.OPENROUTER_API_KEY ? prev.OPENROUTER_API_KEY : '',
          GROQ_API_KEY: keysSet.GROQ_API_KEY ? prev.GROQ_API_KEY : '',
          DEEPSEEK_API_KEY: keysSet.DEEPSEEK_API_KEY ? prev.DEEPSEEK_API_KEY : '',
          COHERE_API_KEY: keysSet.COHERE_API_KEY ? prev.COHERE_API_KEY : '',
          SARVAM_API_KEY: keysSet.SARVAM_API_KEY ? prev.SARVAM_API_KEY : '',
          GCS_SERVICE_ACCOUNT_JSON: keysSet.GCS_SERVICE_ACCOUNT_JSON ? prev.GCS_SERVICE_ACCOUNT_JSON : '',
        }));
      } catch (e) {
        setAdminError(e.response?.data?.error || 'Could not load admin panel data');
      }
    };

    loadAdmin();
  }, [adminOpen, adminSession.authenticated]);

  useEffect(() => {
    if (!adminOpen) return;
    if (adminSession.authenticated) return;
    const clientId = adminBootstrap.google_oauth_client_id;
    if (!clientId) return;

    const scriptId = 'google-gsi-script';
    let script = document.getElementById(scriptId);
    if (!script) {
      script = document.createElement('script');
      script.id = scriptId;
      script.src = 'https://accounts.google.com/gsi/client';
      script.async = true;
      script.defer = true;
      document.body.appendChild(script);
    }

    const initGoogle = () => {
      if (!window.google || !window.google.accounts || !window.google.accounts.id) return;

      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: async (response) => {
          try {
            setAdminLoading(true);
            const auth = await axios.post('/api/admin/auth/google', { id_token: response.credential });
            setAdminSession({ authenticated: true, ...auth.data.user });
            setAdminError('');
          } catch (e) {
            setAdminError(e.response?.data?.error || 'Google sign-in failed');
          } finally {
            setAdminLoading(false);
          }
        },
      });

      const target = document.getElementById('googleSignInButton');
      if (target) {
        target.innerHTML = '';
        window.google.accounts.id.renderButton(target, {
          theme: 'outline',
          size: 'large',
          shape: 'pill',
          text: 'signin_with',
          width: 280,
        });
      }
    };

    if (window.google && window.google.accounts) {
      initGoogle();
    } else {
      script.onload = initGoogle;
    }
  }, [adminOpen, adminSession.authenticated, adminBootstrap.google_oauth_client_id]);

  // Fetch Local AI status when admin opens (and refresh after install/pull)
  useEffect(() => {
    if (adminOpen && adminSession.authenticated) {
      fetchLocalAiStatus();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminOpen, adminSession.authenticated]);

  const saveGuardMode = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/settings/data-guard', { mode: guardMode });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not save Data Guard mode');
    } finally {
      setAdminLoading(false);
    }
  };

  const approveTransfer = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/settings/approve-transfer', { minutes: Number(transferMinutes) || 1440 });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not approve transfer window');
    } finally {
      setAdminLoading(false);
    }
  };

  const saveCredentials = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      const payload = Object.fromEntries(
        Object.entries(keys).filter(([, value]) => String(value || '').trim() !== '')
      );
      await axios.post('/api/settings/credentials', payload);
      setCredentialsSet((prev) => {
        const next = { ...prev };
        Object.keys(payload).forEach((key) => {
          next[key] = true;
        });
        return next;
      });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not save credentials');
    } finally {
      setAdminLoading(false);
    }
  };

  const clearCredential = async (key) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/settings/credentials', { [key]: '' });
      setCredentialsSet((prev) => ({ ...prev, [key]: false }));
      setKeys((prev) => ({ ...prev, [key]: '' }));
    } catch (e) {
      setAdminError(e.response?.data?.error || `Could not clear ${key}`);
    } finally {
      setAdminLoading(false);
    }
  };

  const downloadMemoryDump = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      const res = await axios.get('/api/admin/memory-dump', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = 'prismara_neural_memory_dump.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not download neural memory dump');
    } finally {
      setAdminLoading(false);
    }
  };

  const adminLogout = async () => {
    try {
      await axios.post('/api/admin/logout');
      setAdminSession({ authenticated: false, email: '' });
    } catch {
      setAdminSession({ authenticated: false, email: '' });
    }
  };

  // ── Local AI: Ollama detection, install, model pull/delete, custom probe ──

  const fetchLocalAiStatus = async () => {
    setLocalAiLoading(true);
    setLocalAiError('');
    try {
      const r = await axios.get('/api/admin/local-ai/status');
      const payload = r.data || null;
      setLocalAiStatus(payload);
      if (payload?.hardware_policy) setHardwarePolicy(payload.hardware_policy);
      setSystemDoctor((prev) => (prev ? { ...prev, local_ai: payload } : prev));
    } catch (e) {
      setLocalAiError(e.response?.data?.error || 'Could not load local AI status');
    } finally {
      setLocalAiLoading(false);
    }
  };

  const streamLocalAi = async (url, body, onEvent) => {
    const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    if (!resp.ok || !resp.body) throw new Error(`Stream request failed: ${resp.status}`);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try { onEvent(JSON.parse(t)); } catch { /* ignore */ }
      }
    }
  };

  const installOllama = async () => {
    setLocalAiInstalling(true);
    setLocalAiInstallEvents([]);
    setLocalAiError('');
    try {
      await streamLocalAi('/api/admin/local-ai/install', undefined, (evt) => {
        setLocalAiInstallEvents((prev) => [...prev, evt]);
      });
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.message || 'Install failed');
    } finally {
      setLocalAiInstalling(false);
    }
  };

  const pullModel = async (tag) => {
    setPullingModel(tag);
    setPullProgress({ phase: 'starting', completed: 0, total: 0, status: 'preparing' });
    setLocalAiError('');
    try {
      await streamLocalAi('/api/admin/local-ai/pull-model', { name: tag }, (evt) => {
        if (evt.type === 'pull') {
          setPullProgress({
            phase: evt.phase,
            completed: evt.completed || 0,
            total: evt.total || 0,
            status: evt.status || evt.phase,
          });
        }
      });
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.message || `Pull failed for ${tag}`);
    } finally {
      setPullingModel(null);
      setPullProgress(null);
    }
  };

  const pullModelSet = async (tags = []) => {
    const queue = [...new Set((tags || []).filter(Boolean))];
    if (queue.length === 0) return;
    setLocalAiError('');
    try {
      for (const tag of queue) {
        setPullingModel(tag);
        setPullProgress({ phase: 'starting', completed: 0, total: 0, status: `preparing ${tag}` });
        await streamLocalAi('/api/admin/local-ai/pull-model', { name: tag }, (evt) => {
          if (evt.type === 'pull') {
            setPullProgress({
              phase: evt.phase,
              completed: evt.completed || 0,
              total: evt.total || 0,
              status: evt.status || evt.phase,
            });
          }
        });
      }
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.message || 'Recommended model pull failed');
    } finally {
      setPullingModel(null);
      setPullProgress(null);
    }
  };

  const deleteLocalModel = async (name) => {
    if (!window.confirm(`Remove ${name} from disk? This frees ~${name} of space.`)) return;
    try {
      await axios.delete(`/api/admin/local-ai/model/${encodeURIComponent(name)}`);
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.response?.data?.error || 'Delete failed');
    }
  };

  const updateInstalledModels = async (names = undefined) => {
    setUpdatingModels(true);
    setUpdateProgress({ phase: 'starting', status: 'checking installed models', index: 0, total_models: 0 });
    setLocalAiError('');
    try {
      await streamLocalAi('/api/admin/local-ai/update-models', names ? { names } : {}, (evt) => {
        if (evt.type === 'update') {
          setUpdateProgress({
            phase: evt.phase,
            model: evt.model || '',
            status: evt.status || evt.phase,
            completed: evt.completed || 0,
            total: evt.total || 0,
            index: evt.index || 0,
            total_models: evt.total_models || 0,
            message: evt.message || '',
          });
        }
      });
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.message || 'Model update failed');
    } finally {
      setUpdatingModels(false);
      setUpdateProgress(null);
    }
  };

  const saveHardwareConsent = async (enabled) => {
    setLocalAiLoading(true);
    setLocalAiError('');
    try {
      const r = await axios.post('/api/settings/hardware', {
        hardware_acceleration_consent: Boolean(enabled),
      });
      setHardwarePolicy(r.data || null);
      await fetchLocalAiStatus();
      await fetchSystemDoctor({ silent: true });
    } catch (e) {
      setLocalAiError(e.response?.data?.error || 'Could not update hardware consent');
    } finally {
      setLocalAiLoading(false);
    }
  };

  const probeCustom = async () => {
    try {
      const r = await axios.post('/api/admin/local-ai/detect-custom');
      setCustomProbes(r.data?.endpoints || []);
    } catch (e) {
      setLocalAiError(e.response?.data?.error || 'Probe failed');
    }
  };

  const openSetupLocalAi = () => {
    setAdminOpen(true);
    setSetupRequired(null);
    setTimeout(() => {
      const el = document.getElementById('setup-local-ai-card');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 120);
  };

  const openSystemDoctor = () => {
    setDoctorOpen((v) => !v);
    fetchSystemDoctor({ silent: true });
  };

  const openTrustPage = () => {
    setTrustOpen((v) => !v);
    if (!systemDoctor) fetchSystemDoctor({ silent: true });
  };

  const installRecommendedPack = async () => {
    const st = systemDoctor?.local_ai || localAiStatus;
    if (!adminSession.authenticated) {
      openSetupLocalAi();
      return;
    }
    if (!st?.installed) {
      await installOllama();
      return;
    }
    if (!st?.daemon?.running) {
      openSetupLocalAi();
      return;
    }
    const missing = st?.model_recommendation?.missing_starter_tags || [];
    if (missing.length > 0) {
      await pullModelSet(missing);
    }
  };

  const adoptCustomEndpoint = (ep) => {
    setCustomModelDraft({
      ...EMPTY_CUSTOM_MODEL_DRAFT,
      id: `local-${ep.name.toLowerCase().replace(/\s+/g, '-')}`,
      label: `${ep.name} (local)`,
      base_url: ep.base_url,
      model: (ep.models && ep.models[0]) || 'default',
      tier: 'local',
      auth_type: 'none',
      notes: `Detected on ${ep.host}:${ep.port}`,
    });
  };

  // ── Diagnostics modal: traces + system metrics ─────────────────────────────

  const fetchTraces = async () => {
    setTracesLoading(true);
    try {
      const [tr, sm] = await Promise.all([
        axios.get('/api/admin/traces').catch(() => ({ data: { traces: [] } })),
        axios.get('/api/admin/system-metrics').catch(() => ({ data: null })),
      ]);
      setTraces(Array.isArray(tr.data?.traces) ? tr.data.traces : []);
      setSystemMetrics(sm.data || null);
    } finally {
      setTracesLoading(false);
    }
  };

  const fetchTraceDetail = async (id) => {
    setSelectedTraceId(id);
    setTraceDetail(null);
    try {
      const r = await axios.get(`/api/admin/traces/${encodeURIComponent(id)}`);
      setTraceDetail(r.data || null);
    } catch (e) {
      setTraceDetail({ error: e.response?.data?.error || 'Could not load trace' });
    }
  };

  const openDiagnostics = () => {
    setDiagOpen(true);
    fetchTraces();
  };

  const flashCopyToast = (msg) => {
    setCopyToast(msg);
    setTimeout(() => setCopyToast(''), 1800);
  };

  const buildTraceSummary = (t) => {
    if (!t) return '';
    const lines = [];
    lines.push(`Prismara AI trace ${t.request_id}`);
    lines.push(`started=${t.started_at}  completed=${t.completed_at || '—'}  duration=${t.duration_ms ?? '—'} ms`);
    lines.push(`status=${t.status}  intents=${(t.intents || []).join(',') || '—'}  models=${(t.models_used || []).join(', ') || '—'}`);
    if (t.online_pipeline_mode) lines.push('online_pipeline_mode=true');
    if (t.online_consent) lines.push('online_consent=true');
    if (t.disk_bytes_used) lines.push(`disk_bytes_used=${t.disk_bytes_used}`);
    if (t.workspace) lines.push(`workspace=${t.workspace.folder} (${t.workspace.file_count} files)`);
    lines.push('');
    lines.push(`prompt: ${(t.prompt || '').slice(0, 500)}`);
    lines.push('');
    lines.push('stages:');
    for (const s of (t.stages || [])) {
      const attempts = (s.attempts || []).filter((a) => a.status === 'running' || a.status === 'retry' || a.status === 'done' || a.status === 'error');
      lines.push(`  ${s.name}: ${s.status} agent=${s.agent || '—'} duration_ms=${s.duration_ms ?? '—'}${s.note ? ' note="' + s.note + '"' : ''}${s.error ? ' error="' + s.error + '"' : ''}`);
      if (attempts.length > 1) {
        for (const a of attempts) {
          lines.push(`    └ attempt ${a.status} on ${a.agent || '—'} (${a.duration_ms ?? '—'} ms)${a.error ? ' error="' + a.error + '"' : ''}`);
        }
      }
    }
    if (t.error) {
      lines.push('');
      lines.push(`error: ${t.error}`);
    }
    if (t.final_answer_preview) {
      lines.push('');
      lines.push(`final_answer_preview: ${t.final_answer_preview.slice(0, 600)}`);
    }
    return lines.join('\n');
  };

  const copyTraceJson = async () => {
    if (!traceDetail) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(traceDetail, null, 2));
      flashCopyToast('Full JSON copied');
    } catch (e) {
      flashCopyToast('Copy failed: ' + (e.message || 'clipboard blocked'));
    }
  };

  const copyTraceSummary = async () => {
    if (!traceDetail) return;
    try {
      await navigator.clipboard.writeText(buildTraceSummary(traceDetail));
      flashCopyToast('Summary copied');
    } catch (e) {
      flashCopyToast('Copy failed: ' + (e.message || 'clipboard blocked'));
    }
  };

  const downloadTraceJson = () => {
    if (!traceDetail) return;
    const blob = new Blob([JSON.stringify(traceDetail, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `prismara-trace-${traceDetail.request_id || 'unknown'}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const localRegister = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/admin/register-local', localAuth);
      const auth = await axios.post('/api/admin/auth/local', localAuth);
      setAdminSession({ authenticated: true, ...auth.data.user });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Local admin registration failed');
    } finally {
      setAdminLoading(false);
    }
  };

  const localLogin = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      const auth = await axios.post('/api/admin/auth/local', localAuth);
      setAdminSession({ authenticated: true, ...auth.data.user });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Local admin login failed');
    } finally {
      setAdminLoading(false);
    }
  };

  const saveSsoProfile = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/sso/profiles', ssoDraft);
      const profiles = await axios.get('/api/sso/profiles');
      setSsoProfiles(Array.isArray(profiles.data) ? profiles.data : []);
      setSsoDraft(EMPTY_SSO_DRAFT);
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not save SSO profile');
    } finally {
      setAdminLoading(false);
    }
  };

  const applySsoTemplate = (values) => {
    setSsoDraft((prev) => ({ ...prev, ...values }));
  };

  const refreshSsoStatus = async () => {
    const status = await axios.get('/api/sso/status');
    setSsoStatus(status.data || {});
  };

  const authenticateSsoProfile = async (profile) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      if (profile.flow === 'client_credentials') {
        await axios.post('/api/sso/auth/client-credentials', { profile_id: profile.id });
        await refreshSsoStatus();
      } else {
        const res = await axios.post('/api/sso/auth/device-code/start', { profile_id: profile.id });
        setSsoDeviceFlow({ profileId: profile.id, ...(res.data || {}) });
      }
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not authenticate SSO profile');
    } finally {
      setAdminLoading(false);
    }
  };

  const pollSsoDeviceFlow = async () => {
    if (!ssoDeviceFlow?.profileId) return;
    setAdminLoading(true);
    setAdminError('');
    try {
      const res = await axios.post('/api/sso/auth/device-code/poll', {
        profile_id: ssoDeviceFlow.profileId,
        device_code_response: ssoDeviceFlow,
      });
      if (res.data?.authenticated) {
        setSsoDeviceFlow(null);
        await refreshSsoStatus();
      } else {
        setAdminError(res.data?.message || 'Device authorization did not complete');
      }
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not complete device authorization');
    } finally {
      setAdminLoading(false);
    }
  };

  const revokeSsoProfile = async (id) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/sso/revoke', { profile_id: id });
      await refreshSsoStatus();
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not revoke SSO token');
    } finally {
      setAdminLoading(false);
    }
  };

  const deleteSsoProfile = async (id) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.delete(`/api/sso/profiles/${encodeURIComponent(id)}`);
      setSsoProfiles((prev) => prev.filter((p) => p.id !== id));
      if (ssoDeviceFlow?.profileId === id) {
        setSsoDeviceFlow(null);
      }
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not delete SSO profile');
    } finally {
      setAdminLoading(false);
    }
  };

  const saveIntegration = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/integrations', integrationDraft);
      const ints = await axios.get('/api/integrations');
      setIntegrations(Array.isArray(ints.data) ? ints.data : []);
      setIntegrationDraft({ id: '', name: '', endpoint: '', auth_type: 'none', api_key: '', secret: '', notes: '' });
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not save integration');
    } finally {
      setAdminLoading(false);
    }
  };

  const deleteIntegration = async (id) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.delete(`/api/integrations/${encodeURIComponent(id)}`);
      setIntegrations((prev) => prev.filter((x) => x.id !== id));
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not delete integration');
    } finally {
      setAdminLoading(false);
    }
  };

  const applyCustomModelTemplate = (values) => {
    setCustomModelDraft((prev) => ({ ...prev, ...values }));
  };

  const saveCustomModel = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      let extraHeaders = {};
      if (customModelDraft.extra_headers.trim()) {
        extraHeaders = JSON.parse(customModelDraft.extra_headers);
      }
      const entry = {
        ...customModelDraft,
        capabilities: customModelDraft.capabilities
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean),
        extra_headers: extraHeaders,
      };
      await axios.post('/api/custom-models', entry);
      const models = await axios.get('/api/custom-models');
      setCustomModels(Array.isArray(models.data) ? models.data : []);
      setCustomModelDraft(EMPTY_CUSTOM_MODEL_DRAFT);
    } catch (e) {
      setAdminError(e.response?.data?.error || e.message || 'Could not save custom model');
    } finally {
      setAdminLoading(false);
    }
  };

  const deleteCustomModel = async (id) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.delete(`/api/custom-models/${encodeURIComponent(id)}`);
      setCustomModels((prev) => prev.filter((x) => x.id !== id));
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not delete custom model');
    } finally {
      setAdminLoading(false);
    }
  };

  const testIntegration = async (id) => {
    setAdminLoading(true);
    setAdminError('');
    try {
      const res = await axios.post(`/api/integrations/${encodeURIComponent(id)}/test`, {});
      if (!res.data?.ok) {
        setAdminError(`Integration test failed: ${res.data?.error || 'unknown error'}`);
      }
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not test integration');
    } finally {
      setAdminLoading(false);
    }
  };

  const saveBackupConfig = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      await axios.post('/api/admin/backup-config', backupConfig);
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not save backup preference');
    } finally {
      setAdminLoading(false);
    }
  };

  const runBackupNow = async () => {
    setAdminLoading(true);
    setAdminError('');
    try {
      const res = await axios.post('/api/admin/backup-now', {});
      setBackupLastResult(res.data?.result || null);
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not run backup');
    } finally {
      setAdminLoading(false);
    }
  };

  const testGoogleCloudConnection = async () => {
    setAdminLoading(true);
    setAdminError('');
    setGcsTestResult(null);
    try {
      const res = await axios.post('/api/admin/backup-test-gcs', {});
      setGcsTestResult(res.data || null);
      if (!res.data?.ok) {
        setAdminError(res.data?.error || 'Google Cloud test failed');
      }
    } catch (e) {
      setAdminError(e.response?.data?.error || 'Could not test Google Cloud connection');
    } finally {
      setAdminLoading(false);
    }
  };

  const formatEpoch = (v) => {
    const x = Number(v || 0);
    if (!x) return 'never';
    return new Date(x * 1000).toLocaleString();
  };

  const extractFinalAnswer = (content) => {
    if (!content) return '';
    const marker = '✅ [FINAL RESULT]:';
    const idx = content.indexOf(marker);
    if (idx >= 0) {
      return content.slice(idx + marker.length).trim();
    }
    return content.trim();
  };

  const formatMs = (value) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
    return `${Math.round(Number(value))} ms`;
  };

  const formatParallelism = (parallelism) => {
    if (!parallelism || typeof parallelism !== 'object') return '-';
    const parts = [];
    if (parallelism.processors) parts.push(`processors x${parallelism.processors.limit || 1}`);
    if (parallelism.safety) parts.push(`checks x${parallelism.safety.limit || 1}`);
    return parts.join(', ') || '-';
  };

  const loadFolderByPath = async (pathValue) => {
    if (!pathValue.trim()) return;
    setWorkspaceLoading(true);
    setWorkspaceError('');
    setWorkspace(null);
    try {
      const response = await axios.post('/api/load-folder', { folder_path: pathValue.trim() });
      setWorkspace(response.data);
      setFileTreeOpen(true);
    } catch (err) {
      setWorkspaceError(err.response?.data?.error || 'Could not load folder');
    } finally {
      setWorkspaceLoading(false);
    }
  };

  const handleLoadFolder = async () => {
    const selectedPath = folderPath.trim();
    if (!selectedPath) return;
    if (!usageAgreementAccepted) {
      setPendingFolderPath(selectedPath);
      setShowUsageAgreement(true);
      return;
    }
    await loadFolderByPath(selectedPath);
  };

  const acceptUsageAgreementAndContinue = async () => {
    try {
      localStorage.setItem('prismara_ai_usage_agreement_accepted', 'true');
    } catch {
      // Non-blocking if storage is unavailable.
    }
    setUsageAgreementAccepted(true);
    setShowUsageAgreement(false);
    const selectedPath = pendingFolderPath || folderPath.trim();
    setPendingFolderPath('');
    await loadFolderByPath(selectedPath);
  };

  const declineUsageAgreement = () => {
    setShowUsageAgreement(false);
    setPendingFolderPath('');
  };

  const handleClearWorkspace = () => {
    setWorkspace(null);
    setFolderPath('');
    setWorkspaceError('');
    setFileTreeOpen(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    const requestStart = performance.now();
    const observedAgents = new Set();
    let firstChunkAt = null;

    setLoading(true);
    setError('');
    setResult('');
    setFinalAnswer('');
    setRunMetrics(null);
    setStreamEvents([]);
    setActiveAgent('');
    setRawOutputOpen(false);
    setPipelineStages({});
    setPipelineStarted(false);
    setRequestIntents([]);
    setPipelineWarnings([]);
    setConsentUsages([]);
    setSetupRequired(null);
    setOnlineModeBanner('');
    
    try {
      const payload = { prompt, online_consent: onlineConsent };
      if (workspace) payload.folder_path = workspace.folder;

      const response = await fetch('/api/run-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok || !response.body) {
        throw new Error(`Streaming request failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        if (firstChunkAt === null) {
          firstChunkAt = performance.now();
          setRunMetrics((prev) => ({
            ...(prev || {}),
            request_ms: Math.round(firstChunkAt - requestStart),
          }));
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          let evt;
          try {
            evt = JSON.parse(trimmed);
          } catch {
            continue;
          }

          if (evt.type === 'status') {
            setStreamEvents((prev) => [...prev, evt.message]);
          } else if (evt.type === 'agent') {
            if (evt.agent) observedAgents.add(evt.agent);
            setActiveAgent(evt.agent || '');
            setStreamEvents((prev) => [...prev, `Using agent: ${evt.agent}`]);
          } else if (evt.type === 'stage') {
            setPipelineStarted(true);
            const stageName = evt.name;
            if (!stageName) continue;
            if (evt.agent && !/regex|deterministic/i.test(evt.agent)) {
              observedAgents.add(evt.agent);
            }
            if (evt.status === 'running' && evt.agent) {
              setActiveAgent(evt.agent);
            }
            setPipelineStages((prev) => ({
              ...prev,
              [stageName]: {
                ...(prev[stageName] || {}),
                status: evt.status,
                agent: evt.agent ?? prev[stageName]?.agent,
                duration_ms: evt.duration_ms ?? prev[stageName]?.duration_ms,
                output_preview: evt.output_preview ?? prev[stageName]?.output_preview,
                error: evt.error ?? prev[stageName]?.error,
                note: evt.note ?? prev[stageName]?.note,
              },
            }));
          } else if (evt.type === 'intents') {
            setRequestIntents(Array.isArray(evt.intents) ? evt.intents : []);
          } else if (evt.type === 'warning') {
            setPipelineStarted(true);
            setPipelineWarnings((prev) => [...prev, evt.message || '']);
          } else if (evt.type === 'consent_used') {
            setPipelineStarted(true);
            if (evt.agent) observedAgents.add(evt.agent);
            setConsentUsages((prev) => [...prev, {
              agent: evt.agent || '',
              intent: evt.intent || '',
              duration_ms: evt.duration_ms,
            }]);
          } else if (evt.type === 'setup_required') {
            setSetupRequired({
              message: evt.message || 'No local agents are available.',
              detail: evt.detail || {},
            });
            setRawOutputOpen(true);
          } else if (evt.type === 'online_pipeline_mode') {
            setPipelineStarted(true);
            setOnlineModeBanner(evt.message || 'Running on online providers (consent granted, no local).');
          } else if (evt.type === 'final_answer') {
            setFinalAnswer(evt.content || '');
          } else if (evt.type === 'metrics') {
            setRunMetrics((prev) => ({
              ...(prev || {}),
              ...(evt || {}),
              models: Array.isArray(evt.models) ? evt.models : (prev?.models || []),
            }));
          } else if (evt.type === 'result') {
            const content = evt.content || '';
            const answer = evt.final_answer || extractFinalAnswer(content);
            const renderStart = performance.now();
            setResult(content);
            if (answer) setFinalAnswer(answer);
            requestAnimationFrame(() => {
              const renderMs = Math.round(performance.now() - renderStart);
              const totalMs = Math.round(performance.now() - requestStart);
              setRunMetrics((prev) => ({
                ...(prev || {}),
                render_ms: renderMs,
                total_ms: totalMs,
                request_ms: prev?.request_ms ?? (firstChunkAt ? Math.round(firstChunkAt - requestStart) : null),
                models: (prev?.models && prev.models.length > 0) ? prev.models : Array.from(observedAgents),
              }));
            });
          } else if (evt.type === 'error') {
            setError(evt.message || 'Streaming execution failed');
            setRawOutputOpen(true);
          }
        }
      }
    } catch (err) {
      setError(err.response?.data?.error || err.message || 'An error occurred during execution');
      setRawOutputOpen(true);
    } finally {
      setLoading(false);
      await loadChatHistory({ silent: true });
    }
  };

  const chatTurns = [];
  let pendingChatUser = null;
  for (let i = 0; i < chatHistory.length; i += 1) {
    const message = chatHistory[i];
    if (message?.role === 'user') {
      if (pendingChatUser) chatTurns.push({ user: pendingChatUser, assistant: null });
      pendingChatUser = message;
    } else if ((message?.role === 'assistant' || message?.role === 'error') && pendingChatUser) {
      chatTurns.push({ user: pendingChatUser, assistant: message });
      pendingChatUser = null;
    }
  }
  if (pendingChatUser) chatTurns.push({ user: pendingChatUser, assistant: null });
  const recentChatTurns = chatTurns.slice(-8).reverse();
  const previewText = (text, max = 90) => {
    const compact = String(text || '').replace(/\s+/g, ' ').trim();
    return compact.length > max ? `${compact.slice(0, max)}...` : compact;
  };
  const formatChatTime = (value) => {
    const ts = Number(value || 0);
    return ts ? new Date(ts * 1000).toLocaleString() : '';
  };
  const workspaceStatus = workspace ? `${workspace.file_count} files` : 'Not loaded';
  const executionStatus = loading ? (activeAgent || 'Running') : 'Idle';
  const consentStatus = onlineConsent ? 'Online allowed' : 'Local only';
  const adminStatus = adminSession.authenticated ? 'Signed in' : 'Locked';
  const doctorLocalAi = systemDoctor?.local_ai || localAiStatus || {};
  const doctorCpu = doctorLocalAi.cpu || {};
  const doctorGpu = doctorLocalAi.gpu || {};
  const doctorGpuList = doctorGpu.gpus || [];
  const doctorGpuIgnored = Boolean(doctorGpu.ignored_for_inference || doctorGpu.low_vram);
  const doctorGpuName = doctorGpuList.length > 0
    ? `${doctorGpuList[0].name} (${doctorGpuList[0].vram_gb} GB VRAM)`
    : '';
  const doctorDaemon = doctorLocalAi.daemon || {};
  const doctorRuntime = systemDoctor?.runtime_setup || {};
  const doctorSpeed = systemDoctor?.expected_speed || {};
  const doctorPack = systemDoctor?.recommended_pack || doctorLocalAi.model_recommendation || {};
  const doctorTrust = systemDoctor?.trust || {};
  const doctorChecklist = systemDoctor?.checklist || [];
  const recommendedMissing = doctorPack.missing_starter_tags || [];
  const recommendedStarter = doctorPack.starter_tags || [];
  const doctorRuntimeReady = Boolean(doctorLocalAi.installed && doctorDaemon.running);
  const doctorPrimaryAction = !doctorLocalAi.installed
    ? 'Install runtime'
    : (!doctorDaemon.running ? 'Open runtime controls' : (
      recommendedMissing.length > 0 ? `Install recommended pack (${recommendedMissing.length})` : 'Recommended pack ready'
    ));
  const doctorPrimaryDisabled = (
    localAiInstalling
    || !!pullingModel
    || updatingModels
    || (doctorRuntimeReady && recommendedMissing.length === 0)
  );
  const showPipelinePanel = (
    pipelineStarted
    || Object.keys(pipelineStages).length > 0
    || pipelineWarnings.length > 0
    || consentUsages.length > 0
  );

  return (
    <>
      <div className="bg-gradient"></div>
      <div className="app-container">
        <header className="topbar">
          <div className="brand-lockup">
            <div className="brand-mark"><Sparkles size={20} /></div>
            <div>
              <h1>Prismara AI</h1>
              <p>Local-first agent workbench</p>
            </div>
          </div>
          <div className="topbar-actions">
            <button
              type="button"
              className={`diagnostics-btn ${doctorOpen ? 'active' : ''}`}
              title="System Doctor"
              onClick={openSystemDoctor}
            >
              <Terminal size={18} />
            </button>
            <button
              type="button"
              className={`diagnostics-btn ${trustOpen ? 'active' : ''}`}
              title="Open-source Trust"
              onClick={openTrustPage}
            >
              <ShieldCheck size={18} />
            </button>
            <button
              type="button"
              className="diagnostics-btn"
              title="Diagnostics & Traces"
              onClick={openDiagnostics}
            >
              <Activity size={18} />
            </button>
            <button className="admin-toggle-btn" onClick={() => setAdminOpen((v) => !v)}>
              <Settings2 size={16} /> {adminOpen ? 'Hide Controls' : 'Controls'}
            </button>
          </div>
        </header>

        <section className="status-strip" aria-label="Session status">
          <div className="status-item">
            <FolderOpen size={16} />
            <span>Workspace</span>
            <strong>{workspaceStatus}</strong>
          </div>
          <div className="status-item">
            <History size={16} />
            <span>History</span>
            <strong>{chatTurns.length} saved</strong>
          </div>
          <div className="status-item">
            <Activity size={16} />
            <span>Execution</span>
            <strong>{executionStatus}</strong>
          </div>
          <div className="status-item">
            <ShieldCheck size={16} />
            <span>Mode</span>
            <strong>{consentStatus}</strong>
          </div>
          <div className="status-item">
            <Settings2 size={16} />
            <span>Admin</span>
            <strong>{adminStatus}</strong>
          </div>
        </section>

        {doctorOpen && (
          <section className="system-doctor-panel" aria-label="System Doctor">
            <div className="system-doctor-header">
              <div>
                <h2><Terminal className="icon" /> System Doctor</h2>
                <p>Hardware, runtime, model pack, and expected local speed.</p>
              </div>
              <div className="system-doctor-actions">
                <button
                  type="button"
                  className="clear-btn mini-btn"
                  onClick={() => fetchSystemDoctor()}
                  disabled={systemDoctorLoading}
                >
                  <RefreshCw size={14} /> Refresh
                </button>
                <button
                  type="button"
                  className="load-btn mini-btn"
                  onClick={installRecommendedPack}
                  disabled={doctorPrimaryDisabled || systemDoctorLoading}
                >
                  {localAiInstalling ? 'Installing runtime...' : pullingModel ? `Pulling ${pullingModel}` : doctorPrimaryAction}
                </button>
              </div>
            </div>

            {systemDoctorError && <div className="workspace-error"><AlertCircle size={14} /> {systemDoctorError}</div>}
            {systemDoctorLoading && !systemDoctor && <div className="file-tree-item">Running System Doctor...</div>}

            <div className="doctor-grid">
              <div className="doctor-tile">
                <span>CPU</span>
                <strong>{doctorCpu.name || doctorCpu.architecture || 'Detected CPU'}</strong>
                <em>{doctorCpu.logical_cores ? `${doctorCpu.logical_cores} logical cores` : 'Core count unavailable'}</em>
              </div>
              <div className="doctor-tile">
                <span>RAM</span>
                <strong>{doctorLocalAi.system_ram_gb ? `${doctorLocalAi.system_ram_gb} GB` : 'Unknown'}</strong>
                <em>Used for model-fit recommendations</em>
              </div>
              <div className="doctor-tile">
                <span>GPU</span>
                <strong>
                  {doctorGpuIgnored ? 'Ignored (CPU-only)' : (doctorGpuName || 'CPU path')}
                </strong>
                <em>{doctorGpuIgnored && doctorGpuName ? `${doctorGpuName}. ${doctorGpu.note}` : (doctorGpu.note || 'No GPU acceleration selected')}</em>
              </div>
              <div className="doctor-tile">
                <span>Disk</span>
                <strong>{doctorLocalAi.disk_free_gb || 0} GB free</strong>
                <em>{doctorLocalAi.model_dir || doctorRuntime.model_storage || 'Model directory pending'}</em>
              </div>
              <div className={`doctor-tile ${doctorRuntimeReady ? 'ok' : 'warn'}`}>
                <span>Runtime</span>
                <strong>{doctorRuntimeReady ? 'Ollama ready' : (doctorLocalAi.installed ? 'Ollama not running' : 'Runtime needed')}</strong>
                <em>{doctorRuntime.offline_installer_available ? 'Offline installer available' : 'Managed download if needed'}</em>
              </div>
              <div className="doctor-tile">
                <span>Expected Speed</span>
                <strong>{doctorSpeed.label || 'Checking'}</strong>
                <em>{doctorSpeed.local_models || 'Speed estimate appears after hardware scan'}</em>
              </div>
            </div>

            <div className="doctor-body-grid">
              <div className="doctor-section">
                <div className="doctor-section-head">
                  <strong>Recommended model pack</strong>
                  <span>{doctorPack.starter_total_gb || 0} GB starter</span>
                </div>
                <div className="local-ai-rec-tags">
                  {recommendedStarter.length > 0 ? recommendedStarter.map((tag) => (
                    <span key={tag} className={`local-ai-rec-tag ${recommendedMissing.includes(tag) ? '' : 'installed'}`}>{tag}</span>
                  )) : <span className="doctor-muted">No recommendation yet</span>}
                </div>
                <p>{doctorPack.summary || 'Prismara AI will recommend compact local models after hardware detection completes.'}</p>
                {doctorPack.primary_tag && <p><strong>Primary:</strong> {doctorPack.primary_tag}</p>}
              </div>

              <div className="doctor-section">
                <div className="doctor-section-head">
                  <strong>Readiness</strong>
                  <span>{doctorChecklist.filter((c) => c.state === 'ok').length}/{doctorChecklist.length || 0} ok</span>
                </div>
                <div className="doctor-checklist">
                  {doctorChecklist.map((item) => (
                    <div key={item.id} className={`doctor-check doctor-check-${item.state}`}>
                      {item.state === 'ok' ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                      <div>
                        <strong>{item.label}</strong>
                        <span>{item.detail}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {doctorRuntime.prereq_note && (
              <div className="doctor-runtime-note">
                <strong>Portable runtime:</strong> {doctorRuntime.prereq_note}
              </div>
            )}
          </section>
        )}

        {trustOpen && (
          <section className="trust-panel" aria-label="Open-source trust">
            <div className="system-doctor-header">
              <div>
                <h2><ShieldCheck className="icon" /> Open-source Trust</h2>
                <p>Local-first behavior, encryption, online consent, transfer limits, and license.</p>
              </div>
              <button type="button" className="clear-btn mini-btn" onClick={() => setTrustOpen(false)}>
                <XIcon size={14} /> Close
              </button>
            </div>
            <div className="trust-grid">
              <div className="trust-section">
                <strong>Local by default</strong>
                <p>{(doctorTrust.stays_local || []).join(', ') || 'Chat history, credentials, memory, logs, backups, and local models stay in the local prismara folder.'}</p>
              </div>
              <div className="trust-section">
                <strong>Encrypted state</strong>
                <p>{doctorTrust.encrypted || 'Machine-bound encrypted storage protects Prismara AI state at rest.'}</p>
              </div>
              <div className="trust-section">
                <strong>Online consent</strong>
                <p>{doctorTrust.online || 'Cloud providers run only after explicit consent or configured integrations.'}</p>
              </div>
              <div className="trust-section">
                <strong>Transfer mode</strong>
                <p>{doctorTrust.transfer || 'Transfer mode is time-limited and cannot prevent OS-level file copying by an administrator.'}</p>
              </div>
              <div className="trust-section">
                <strong>License</strong>
                <p>{doctorTrust.license || 'Apache-2.0'} - {doctorTrust.copyright || 'Copyright 2026 Rajesh Kumar Mohanty'}</p>
              </div>
            </div>
          </section>
        )}

        {adminOpen && (
          <section className="workspace-panel admin-panel">
            <div className="workspace-header">
              <h2><ShieldCheck className="icon" /> Admin Control Panel</h2>
              <span className="workspace-badge">Google SSO (MVP1)</span>
            </div>

            {!adminSession.authenticated ? (
              <div className="admin-login-box">
                {!adminBootstrap.google_oauth_client_id && (
                  <div className="workspace-error">
                    <AlertCircle size={14} /> Configure GOOGLE_OAUTH_CLIENT_ID in credentials before admin login.
                  </div>
                )}
                <div className="admin-login-title">Google SSO (Primary)</div>
                <div id="googleSignInButton"></div>
                <a href="https://accounts.google.com/signup" target="_blank" rel="noreferrer" className="admin-link">
                  Create Google account
                </a>

                <div className="admin-divider">or</div>

                <div className="admin-login-title">Manual Admin Credentials (Fallback)</div>
                <div className="admin-keys-grid">
                  <div>
                    <label>username</label>
                    <input className="folder-input" value={localAuth.username} onChange={(e) => setLocalAuth((p) => ({ ...p, username: e.target.value }))} />
                  </div>
                  <div>
                    <label>password</label>
                    <input type="password" className="folder-input" value={localAuth.password} onChange={(e) => setLocalAuth((p) => ({ ...p, password: e.target.value }))} />
                  </div>
                </div>
                <div className="admin-auth-actions">
                  <button className="load-btn" onClick={localLogin} disabled={adminLoading}>Sign in (manual)</button>
                  <button className="clear-btn" onClick={localRegister} disabled={adminLoading}>Create local admin</button>
                </div>
              </div>
            ) : (
              <>
                <div className="admin-user-row">
                  <div>Signed in as <strong>{adminSession.email}</strong></div>
                  <button className="clear-btn" onClick={adminLogout}><LogOut size={14} /> Logout</button>
                </div>

                <div className="admin-grid">
                  <div className="admin-card">
                    <h3>Guardrails</h3>
                    <label>Data Guard Mode</label>
                    <select value={guardMode} onChange={(e) => setGuardMode(e.target.value)} className="folder-input">
                      <option value="off">off</option>
                      <option value="auto">auto</option>
                      <option value="always">always</option>
                      <option value="strict">strict</option>
                    </select>
                    <button className="load-btn" onClick={saveGuardMode} disabled={adminLoading}>Save Guard Mode</button>
                  </div>

                  <div className="admin-card">
                    <h3>Migration Window</h3>
                    <label>Allowed transfer window (minutes)</label>
                    <input
                      type="number"
                      min="1"
                      max="1440"
                      value={transferMinutes}
                      onChange={(e) => setTransferMinutes(e.target.value)}
                      className="folder-input"
                    />
                    <button className="load-btn" onClick={approveTransfer} disabled={adminLoading}>Approve Migration Window</button>
                  </div>

                  <div className="admin-card">
                    <h3>Neural Memory</h3>
                    <p>Export complete neural memory dump.</p>
                    <button className="load-btn" onClick={downloadMemoryDump} disabled={adminLoading}><Download size={14} /> Download Dump</button>
                  </div>

                  <div className="admin-card">
                    <h3>Storage Resilience</h3>
                    {storageHealth ? (
                      <div className="file-tree">
                        <div className="file-tree-item">Backups available: {storageHealth.backup_files}</div>
                        <div className="file-tree-item">Config file: {storageHealth.critical_files?.config_exists ? 'ok' : 'missing (auto-heal on access)'}</div>
                        <div className="file-tree-item">Credentials file: {storageHealth.critical_files?.credentials_exists ? 'ok' : 'missing (auto-heal on access)'}</div>
                        <div className="file-tree-item">Memory file: {storageHealth.critical_files?.memory_exists ? 'ok' : 'missing (auto-heal on access)'}</div>
                      </div>
                    ) : (
                      <p>Storage health unavailable (requires admin session).</p>
                    )}

                    <label>Backup preference</label>
                    <select className="folder-input" value={backupConfig.mode} onChange={(e) => setBackupConfig((p) => ({ ...p, mode: e.target.value }))}>
                      <option value="local">local (inside prismara)</option>
                      <option value="google_cloud">google_cloud only</option>
                      <option value="both">both local + google_cloud</option>
                    </select>

                    <label>Google Cloud bucket</label>
                    <input className="folder-input" value={backupConfig.gcs_bucket || ''} onChange={(e) => setBackupConfig((p) => ({ ...p, gcs_bucket: e.target.value }))} placeholder="your-gcs-bucket" />

                    <label>Google Cloud path prefix</label>
                    <input className="folder-input" value={backupConfig.gcs_prefix || ''} onChange={(e) => setBackupConfig((p) => ({ ...p, gcs_prefix: e.target.value }))} placeholder="prismara-backups" />

                    <div className="admin-auth-actions">
                      <button className="load-btn" onClick={saveBackupConfig} disabled={adminLoading}>Save Backup Preference</button>
                      <button className="clear-btn" onClick={testGoogleCloudConnection} disabled={adminLoading}>Test Google Cloud Connection</button>
                      <button className="clear-btn" onClick={runBackupNow} disabled={adminLoading}>Run Backup Now</button>
                    </div>

                    {gcsTestResult && (
                      <div className="file-tree">
                        <div className="file-tree-item">GCS test: {gcsTestResult.ok ? 'ok' : 'failed'}</div>
                        {gcsTestResult.bucket && <div className="file-tree-item">Bucket: {gcsTestResult.bucket}</div>}
                        {gcsTestResult.auth_source && <div className="file-tree-item">Auth source: {gcsTestResult.auth_source}</div>}
                        {gcsTestResult.error && <div className="file-tree-item">Error: {gcsTestResult.error}</div>}
                      </div>
                    )}

                    <div className="file-tree">
                      <div className="file-tree-item">Last successful cloud backup: {formatEpoch(backupConfig.last_cloud_backup_at)}</div>
                      {backupConfig.last_cloud_backup_bucket && <div className="file-tree-item">Last backup bucket: {backupConfig.last_cloud_backup_bucket}</div>}
                      {backupConfig.last_cloud_backup_blob && <div className="file-tree-item">Last backup object: {backupConfig.last_cloud_backup_blob}</div>}
                      <div className="file-tree-item">Last GCS connectivity test: {formatEpoch(backupConfig.last_gcs_test_at)} {backupConfig.last_gcs_test_ok === null ? '' : backupConfig.last_gcs_test_ok ? '(ok)' : '(failed)'}</div>
                    </div>

                    {backupLastResult && (
                      <div className="file-tree">
                        <div className="file-tree-item">Mode: {backupLastResult.mode}</div>
                        {backupLastResult.local && <div className="file-tree-item">Local: {backupLastResult.local.ok ? 'ok' : `failed: ${backupLastResult.local.error}`}</div>}
                        {backupLastResult.google_cloud && <div className="file-tree-item">Google Cloud: {backupLastResult.google_cloud.ok ? 'ok' : `failed: ${backupLastResult.google_cloud.error}`}</div>}
                      </div>
                    )}
                  </div>
                </div>

                <div className="admin-card">
                  <h3>Provider API Access</h3>
                  <div className="provider-grid">
                    {PROVIDER_ACCESS.map((provider) => {
                      const configured = Boolean(credentialsSet[provider.key] || keys[provider.key]);
                      return (
                        <div className="provider-card" key={provider.id}>
                          <div className="provider-card-head">
                            <strong>{provider.name}</strong>
                            <span className={configured ? 'status-pill ok' : 'status-pill muted'}>
                              {configured ? 'configured' : 'not set'}
                            </span>
                          </div>
                          <div className="provider-meta">
                            <span>{provider.key}</span>
                            <span>{provider.sso}</span>
                          </div>
                          <div className="provider-note">{provider.note}</div>
                          <div className="provider-links">
                            <a href={provider.consumerUrl} target="_blank" rel="noreferrer" className="admin-link">App</a>
                            <a href={provider.consoleUrl} target="_blank" rel="noreferrer" className="admin-link">API keys</a>
                            <a href={provider.docsUrl} target="_blank" rel="noreferrer" className="admin-link">Docs</a>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="admin-card">
                  <h3>Configuration / API Keys</h3>
                  <p>For Google Cloud backup, set GCS_SERVICE_ACCOUNT_JSON with the full service account JSON string (optional if using Application Default Credentials).</p>
                  <div className="admin-keys-grid">
                    {Object.keys(keys).map((k) => (
                      <div key={k}>
                        <label className="key-label">
                          <span>{k}</span>
                          {credentialsSet[k] && <span className="status-pill ok">saved</span>}
                        </label>
                        <input
                          type={isSensitiveCredentialKey(k) ? 'password' : 'text'}
                          className="folder-input"
                          value={keys[k]}
                          onChange={(e) => setKeys((prev) => ({ ...prev, [k]: e.target.value }))}
                          placeholder={`Enter ${k}`}
                          autoComplete="off"
                          spellCheck="false"
                        />
                        {credentialsSet[k] && (
                          <button className="clear-btn mini-btn inline-clear" onClick={() => clearCredential(k)} disabled={adminLoading}>Clear saved value</button>
                        )}
                      </div>
                    ))}
                  </div>
                  <button className="load-btn" onClick={saveCredentials} disabled={adminLoading}>Save Configuration</button>
                </div>

                <div className="admin-card">
                  <h3>SSO Status</h3>
                  <div className="file-tree">
                    {ssoProfiles.length === 0 && <div className="file-tree-item">No SSO profiles configured.</div>}
                    {ssoProfiles.map((p) => (
                      <div className="file-tree-item" key={p.id}>
                        {p.label || p.id} ({p.flow}) - {ssoStatus[p.id]?.authenticated ? 'authenticated' : 'not authenticated'}
                        <button className="clear-btn mini-btn" onClick={() => authenticateSsoProfile(p)} disabled={adminLoading}>
                          {p.flow === 'client_credentials' ? 'Authenticate' : 'Start device code'}
                        </button>
                        <button className="clear-btn mini-btn" onClick={() => revokeSsoProfile(p.id)} disabled={adminLoading}>Revoke</button>
                        <button className="clear-btn mini-btn" onClick={() => deleteSsoProfile(p.id)}>Delete</button>
                      </div>
                    ))}
                  </div>
                  {ssoDeviceFlow && (
                    <div className="device-flow-box">
                      <div><strong>{ssoDeviceFlow.profileId}</strong></div>
                      {ssoDeviceFlow.user_code && <div>User code: {ssoDeviceFlow.user_code}</div>}
                      {(ssoDeviceFlow.verification_uri || ssoDeviceFlow.verification_url) && (
                        <a href={ssoDeviceFlow.verification_uri || ssoDeviceFlow.verification_url} target="_blank" rel="noreferrer" className="admin-link">
                          Open verification page
                        </a>
                      )}
                      {ssoDeviceFlow.message && <div>{ssoDeviceFlow.message}</div>}
                      <button className="load-btn" onClick={pollSsoDeviceFlow} disabled={adminLoading}>Complete Device Authorization</button>
                    </div>
                  )}
                </div>

                <div className="admin-card">
                  <h3>Add SSO Profile (N supported)</h3>
                  <div className="template-row">
                    {SSO_TEMPLATES.map((template) => (
                      <button className="clear-btn mini-btn" key={template.name} onClick={() => applySsoTemplate(template.values)}>
                        {template.name}
                      </button>
                    ))}
                  </div>
                  <div className="admin-keys-grid">
                    <div><label>id</label><input className="folder-input" value={ssoDraft.id} onChange={(e) => setSsoDraft((p) => ({ ...p, id: e.target.value }))} /></div>
                    <div><label>label</label><input className="folder-input" value={ssoDraft.label} onChange={(e) => setSsoDraft((p) => ({ ...p, label: e.target.value }))} /></div>
                    <div>
                      <label>flow</label>
                      <select className="folder-input" value={ssoDraft.flow} onChange={(e) => setSsoDraft((p) => ({ ...p, flow: e.target.value }))}>
                        <option value="client_credentials">client_credentials</option>
                        <option value="device_code">device_code</option>
                      </select>
                    </div>
                    <div><label>token_url</label><input className="folder-input" value={ssoDraft.token_url} onChange={(e) => setSsoDraft((p) => ({ ...p, token_url: e.target.value }))} /></div>
                    <div><label>device_auth_url</label><input className="folder-input" value={ssoDraft.device_auth_url} onChange={(e) => setSsoDraft((p) => ({ ...p, device_auth_url: e.target.value }))} /></div>
                    <div><label>client_id</label><input className="folder-input" value={ssoDraft.client_id} onChange={(e) => setSsoDraft((p) => ({ ...p, client_id: e.target.value }))} /></div>
                    <div><label>client_secret</label><input className="folder-input" value={ssoDraft.client_secret} onChange={(e) => setSsoDraft((p) => ({ ...p, client_secret: e.target.value }))} /></div>
                    <div><label>scope</label><input className="folder-input" value={ssoDraft.scope} onChange={(e) => setSsoDraft((p) => ({ ...p, scope: e.target.value }))} /></div>
                  </div>
                  <button className="load-btn" onClick={saveSsoProfile} disabled={adminLoading}>Save SSO Profile</button>
                </div>

                <div className="admin-card" id="setup-local-ai-card">
                  <h3>Setup Local AI</h3>
                  {localAiError && <div className="error-message">{localAiError}</div>}
                  {localAiLoading && !localAiStatus && (
                    <div className="file-tree-item">Loading local AI status…</div>
                  )}
                  {localAiStatus && (() => {
                    const st = localAiStatus;
                    const installed = !!st.installed;
                    const daemonUp = !!(st.daemon && st.daemon.running);
                    const pulledModels = (st.daemon && st.daemon.models) || [];
                    const catalog = st.catalog || [];
                    const recommendation = st.model_recommendation || {};
                    const roleCoverage = st.role_coverage || {};
                    const gpu = st.gpu || {};
                    const gpus = gpu.gpus || [];
                    const lowVramGpu = Boolean(gpu.low_vram);
                    const gpuIgnored = Boolean(gpu.ignored_for_inference || lowVramGpu);
                    const policy = st.hardware_policy || hardwarePolicy || {};
                    const consentOn = Boolean(policy.hardware_acceleration_consent);
                    const gpuAllowed = Boolean(policy.ollama_gpu_allowed);
                    const modelPulled = (tag) => pulledModels.some((p) => (
                      p.name === tag
                      || p.name === `${tag}:latest`
                      || p.name.startsWith(tag.split(':')[0] + ':')
                    ));
                    const starterMissing = (recommendation.missing_starter_tags || []).filter((tag) => !modelPulled(tag));
                    const coverageMissing = (recommendation.missing_coverage_tags || []).filter((tag) => !modelPulled(tag));
                    const pct = (pp) => (pp && pp.total ? Math.min(100, (pp.completed / pp.total) * 100) : 0);
                    return (
                      <>
                        <div className="local-ai-summary">
                          <div className={`local-ai-pill ${installed ? 'ok' : 'bad'}`}>
                            {installed ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                            {installed ? 'Ollama installed' : 'Ollama not installed'}
                          </div>
                          <div className={`local-ai-pill ${daemonUp ? 'ok' : 'bad'}`}>
                            {daemonUp ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                            {daemonUp ? `Daemon up (${pulledModels.length} models)` : 'Daemon down'}
                          </div>
                          <div className="local-ai-pill neutral">
                            Disk free: {st.disk_free_gb} GB
                          </div>
                          <div className={`local-ai-pill ${gpuIgnored ? 'warn' : 'neutral'}`}>
                            GPU: {gpuIgnored ? 'Ignored (CPU-only)' : (gpus.length > 0 ? `${gpus[0].name} (${gpus[0].vram_gb} GB VRAM)` : 'CPU mode')}
                          </div>
                          <div className={`local-ai-pill ${gpuAllowed ? 'ok' : consentOn ? 'warn' : 'neutral'}`}>
                            Runtime: {gpuAllowed ? 'GPU allowed' : gpuIgnored ? 'CPU-only' : 'CPU enforced'}
                          </div>
                          <div className="local-ai-pill neutral">
                            Models dir: {st.model_dir}
                          </div>
                        </div>

                        <div className={`local-ai-hardware-note ${lowVramGpu ? 'warn' : ''}`}>
                          <div className="local-ai-hardware-title">
                            <Activity size={14} /> Runtime fit
                          </div>
                          <div>{gpu.note || 'CPU-oriented local model recommendations are active.'}</div>
                          {st.system_ram_gb > 0 && (
                            <div>System RAM detected: {st.system_ram_gb} GB. Model pulls remain stored under the configured models directory.</div>
                          )}
                        </div>

                        <div className={`local-ai-consent ${consentOn && !gpuIgnored ? 'on' : ''}`}>
                          <div>
                            <div className="local-ai-consent-title">Hardware acceleration consent</div>
                            <div>{policy.reason || 'Prismara AI will keep Ollama on the CPU path until consent is granted.'}</div>
                          </div>
                          <button
                            type="button"
                            className={`${consentOn ? 'clear-btn' : 'load-btn'} mini-btn`}
                            onClick={() => saveHardwareConsent(!consentOn)}
                            disabled={localAiLoading || gpuIgnored || !policy.gpu_useful}
                          >
                            {gpuIgnored ? 'GPU ignored' : !policy.gpu_useful ? 'No useful GPU' : consentOn ? 'Revoke consent' : 'Allow acceleration'}
                          </button>
                        </div>

                        {recommendation.primary_tag && (
                          <div className="local-ai-recommendation">
                            <div className="local-ai-recommendation-head">
                              <div>
                                <div className="local-ai-recommendation-title">Recommended for this machine</div>
                                <div>{recommendation.profile_title || 'Hardware-aware model plan'}: {recommendation.summary}</div>
                              </div>
                              <span className="local-ai-rec-primary">Primary: {recommendation.primary_tag}</span>
                            </div>
                            <div className="local-ai-rec-grid">
                              <div className="local-ai-rec-set">
                                <div className="local-ai-rec-set-head">
                                  <strong>Starter set</strong>
                                  <span>{recommendation.starter_total_gb || 0} GB</span>
                                </div>
                                <div className="local-ai-rec-tags">
                                  {(recommendation.starter_tags || []).map((tag) => (
                                    <span key={tag} className={`local-ai-rec-tag ${modelPulled(tag) ? 'installed' : ''}`}>{tag}</span>
                                  ))}
                                </div>
                                <button
                                  type="button"
                                  className="load-btn mini-btn"
                                  onClick={() => pullModelSet(starterMissing)}
                                  disabled={!daemonUp || !!pullingModel || updatingModels || starterMissing.length === 0}
                                >
                                  {starterMissing.length === 0 ? 'Starter ready' : `Pull starter (${starterMissing.length})`}
                                </button>
                              </div>
                              <div className="local-ai-rec-set">
                                <div className="local-ai-rec-set-head">
                                  <strong>Full coverage</strong>
                                  <span>{recommendation.coverage_total_gb || 0} GB</span>
                                </div>
                                <div className="local-ai-rec-tags">
                                  {(recommendation.coverage_tags || []).map((tag) => (
                                    <span key={tag} className={`local-ai-rec-tag ${modelPulled(tag) ? 'installed' : ''}`}>{tag}</span>
                                  ))}
                                </div>
                                <button
                                  type="button"
                                  className="clear-btn mini-btn"
                                  onClick={() => pullModelSet(coverageMissing)}
                                  disabled={!daemonUp || !!pullingModel || updatingModels || coverageMissing.length === 0}
                                >
                                  {coverageMissing.length === 0 ? 'Coverage ready' : `Pull missing (${coverageMissing.length})`}
                                </button>
                              </div>
                            </div>
                            {(recommendation.covered_roles || []).length > 0 && (
                              <div className="local-ai-rec-roles">
                                {(recommendation.covered_roles || []).map((role) => (
                                  <span key={role} className="local-ai-role-chip">{role}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}

                        {Object.keys(roleCoverage).length > 0 && (
                          <div className="local-ai-roles">
                            <strong>Roles covered locally:</strong>{' '}
                            {Object.keys(roleCoverage).sort().map((role) => (
                              <span key={role} className="local-ai-role-chip">{role}</span>
                            ))}
                          </div>
                        )}

                        {!installed && (
                          <div className="local-ai-install">
                            <p>Ollama is the recommended local runtime. The installer is ~600 MB and runs per-user without admin rights.</p>
                            <button
                              type="button"
                              className="load-btn"
                              onClick={installOllama}
                              disabled={localAiInstalling}
                            >
                              {localAiInstalling ? 'Installing…' : 'Install Ollama'}
                            </button>
                            {localAiInstalling && localAiInstallEvents.length > 0 && (
                              <div className="local-ai-install-log">
                                {localAiInstallEvents.slice(-5).map((evt, i) => (
                                  <div key={i}>
                                    [{evt.phase}] {evt.message || evt.status || ''}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}

                        {installed && pulledModels.length > 0 && (
                          <div className="local-ai-pulled">
                            <div className="local-ai-section-head">
                              <strong>Pulled models</strong>
                              <button
                                type="button"
                                className="load-btn mini-btn"
                                onClick={() => updateInstalledModels()}
                                disabled={!!pullingModel || updatingModels}
                              >
                                {updatingModels ? 'Updating...' : 'Update all'}
                              </button>
                            </div>
                            {updatingModels && updateProgress && (
                              <div className="local-ai-card-progress">
                                <div className="local-ai-progress-bar"><div className="local-ai-progress-fill" style={{ width: `${pct(updateProgress)}%` }} /></div>
                                <div className="local-ai-progress-text">
                                  {updateProgress.total_models > 0 && (
                                    <span>{updateProgress.index}/{updateProgress.total_models} </span>
                                  )}
                                  {updateProgress.model || 'models'} — {updateProgress.message || updateProgress.status || updateProgress.phase}
                                  {updateProgress.total > 0 && (
                                    <span> ({(updateProgress.completed / (1024 ** 2)).toFixed(0)} / {(updateProgress.total / (1024 ** 2)).toFixed(0)} MB)</span>
                                  )}
                                </div>
                              </div>
                            )}
                            <table className="local-ai-table">
                              <thead><tr><th>Name</th><th>Size</th><th></th></tr></thead>
                              <tbody>
                                {pulledModels.map((m) => (
                                  <tr key={m.name}>
                                    <td>{m.name}</td>
                                    <td>{(m.size_bytes / (1024 ** 3)).toFixed(2)} GB</td>
                                    <td>
                                      <button
                                        type="button"
                                        className="clear-btn mini-btn"
                                        onClick={() => updateInstalledModels([m.name])}
                                        disabled={!!pullingModel || updatingModels}
                                      >Update</button>
                                      <button
                                        type="button"
                                        className="clear-btn mini-btn"
                                        onClick={() => deleteLocalModel(m.name)}
                                        disabled={!!pullingModel || updatingModels}
                                      >Remove</button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}

                        {installed && (
                          <div className="local-ai-catalog">
                            <strong>Recommended models</strong> — pick any to fill more role pools.
                            <div className="local-ai-catalog-grid">
                              {catalog.map((m) => {
                                const isPulled = modelPulled(m.tag);
                                const isPulling = pullingModel === m.tag;
                                const isKnownBroken = Boolean(m.known_broken);
                                const isGpuLimited = Boolean(m.gpu_limited);
                                const isHardwareRecommended = Boolean(m.hardware_recommended);
                                return (
                                  <div key={m.tag} className={`local-ai-card ${isPulled ? 'pulled' : ''} ${m.recommended ? 'recommended' : ''} ${isHardwareRecommended ? 'hardware-recommended' : ''} ${isKnownBroken ? 'disabled' : ''} ${isGpuLimited ? 'gpu-limited' : ''}`}>
                                    <div className="local-ai-card-head">
                                      <span className="local-ai-card-name">{m.tag}</span>
                                      <span className="local-ai-card-size">{m.size_gb} GB</span>
                                    </div>
                                    {isHardwareRecommended && (
                                      <div className="local-ai-card-rec-badge">
                                        {m.recommendation_label || 'Recommended'} for this hardware
                                      </div>
                                    )}
                                    <div className="local-ai-card-summary">{m.summary}</div>
                                    {m.runtime_note && <div className="local-ai-runtime-note">{m.runtime_note}</div>}
                                    <div className="local-ai-card-roles">
                                      {(m.roles || []).map((r) => (
                                        <span key={r} className="local-ai-role-chip">{r}</span>
                                      ))}
                                    </div>
                                    {isKnownBroken ? (
                                      <div className="local-ai-card-status muted">Disabled</div>
                                    ) : isPulled ? (
                                      <div className="local-ai-card-status ok">Pulled ✓</div>
                                    ) : isPulling ? (
                                      <div className="local-ai-card-progress">
                                        <div className="local-ai-progress-bar"><div className="local-ai-progress-fill" style={{ width: `${pct(pullProgress)}%` }} /></div>
                                        <div className="local-ai-progress-text">
                                          {pullProgress?.status || 'pulling…'}{' '}
                                          {pullProgress?.total > 0 && (
                                            <span>({(pullProgress.completed / (1024 ** 2)).toFixed(0)} / {(pullProgress.total / (1024 ** 2)).toFixed(0)} MB)</span>
                                          )}
                                        </div>
                                      </div>
                                    ) : (
                                      <button
                                        type="button"
                                        className="load-btn mini-btn"
                                        onClick={() => pullModel(m.tag)}
                                        disabled={!!pullingModel || updatingModels || m.size_gb > st.disk_free_gb - 1}
                                      >
                                        {m.size_gb > st.disk_free_gb - 1 ? 'Insufficient disk' : `Pull (${m.size_gb} GB)`}
                                      </button>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}

                        <div className="local-ai-custom-probe">
                          <strong>Custom local endpoints</strong>{' '}
                          <button type="button" className="clear-btn mini-btn" onClick={probeCustom}>Probe common ports</button>
                          {(st.custom_endpoints || []).length > 0 && (
                            <div className="local-ai-probe-results">
                              <em>Detected (during status fetch):</em>
                              {(st.custom_endpoints || []).map((ep) => (
                                <div key={ep.base_url} className="local-ai-probe-row">
                                  <span>{ep.name} → {ep.base_url} ({(ep.models || []).length} models)</span>
                                  <button type="button" className="load-btn mini-btn" onClick={() => adoptCustomEndpoint(ep)}>Add as Custom Model</button>
                                </div>
                              ))}
                            </div>
                          )}
                          {customProbes.length > 0 && customProbes !== (st.custom_endpoints || []) && (
                            <div className="local-ai-probe-results">
                              <em>Probed now:</em>
                              {customProbes.map((ep) => (
                                <div key={ep.base_url} className="local-ai-probe-row">
                                  <span>{ep.name} → {ep.base_url} ({(ep.models || []).length} models)</span>
                                  <button type="button" className="load-btn mini-btn" onClick={() => adoptCustomEndpoint(ep)}>Add as Custom Model</button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="local-ai-refresh">
                          <button type="button" className="clear-btn mini-btn" onClick={fetchLocalAiStatus}>↻ Refresh status</button>
                        </div>
                      </>
                    );
                  })()}
                </div>

                <div className="admin-card">
                  <h3>Custom API Models</h3>
                  <div className="template-row">
                    {CUSTOM_MODEL_TEMPLATES.map((template) => (
                      <button className="clear-btn mini-btn" key={template.name} onClick={() => applyCustomModelTemplate(template.values)}>
                        {template.name}
                      </button>
                    ))}
                  </div>
                  <div className="admin-keys-grid">
                    <div><label>id</label><input className="folder-input" value={customModelDraft.id} onChange={(e) => setCustomModelDraft((p) => ({ ...p, id: e.target.value }))} /></div>
                    <div><label>label</label><input className="folder-input" value={customModelDraft.label} onChange={(e) => setCustomModelDraft((p) => ({ ...p, label: e.target.value }))} /></div>
                    <div><label>base_url</label><input className="folder-input" value={customModelDraft.base_url} onChange={(e) => setCustomModelDraft((p) => ({ ...p, base_url: e.target.value }))} /></div>
                    <div><label>model</label><input className="folder-input" value={customModelDraft.model} onChange={(e) => setCustomModelDraft((p) => ({ ...p, model: e.target.value }))} /></div>
                    <div>
                      <label>tier</label>
                      <select className="folder-input" value={customModelDraft.tier} onChange={(e) => setCustomModelDraft((p) => ({ ...p, tier: e.target.value }))}>
                        <option value="local">local</option>
                        <option value="free_cloud">free_cloud</option>
                        <option value="paid">paid</option>
                        <option value="custom">custom</option>
                      </select>
                    </div>
                    <div>
                      <label>auth_type</label>
                      <select className="folder-input" value={customModelDraft.auth_type} onChange={(e) => setCustomModelDraft((p) => ({ ...p, auth_type: e.target.value }))}>
                        <option value="api_key">api_key</option>
                        <option value="sso_client_creds">sso_client_creds</option>
                        <option value="sso_device_code">sso_device_code</option>
                        <option value="none">none</option>
                      </select>
                    </div>
                    <div><label>api_key</label><input className="folder-input" value={customModelDraft.api_key} onChange={(e) => setCustomModelDraft((p) => ({ ...p, api_key: e.target.value }))} /></div>
                    <div><label>sso_profile_id</label><input className="folder-input" value={customModelDraft.sso_profile_id} onChange={(e) => setCustomModelDraft((p) => ({ ...p, sso_profile_id: e.target.value }))} /></div>
                    <div><label>capabilities</label><input className="folder-input" value={customModelDraft.capabilities} onChange={(e) => setCustomModelDraft((p) => ({ ...p, capabilities: e.target.value }))} /></div>
                    <div><label>extra_headers JSON</label><input className="folder-input" value={customModelDraft.extra_headers} onChange={(e) => setCustomModelDraft((p) => ({ ...p, extra_headers: e.target.value }))} /></div>
                    <div><label>notes</label><input className="folder-input" value={customModelDraft.notes} onChange={(e) => setCustomModelDraft((p) => ({ ...p, notes: e.target.value }))} /></div>
                  </div>
                  <button className="load-btn" onClick={saveCustomModel} disabled={adminLoading}>Save Custom Model</button>
                  <div className="file-tree">
                    {customModels.length === 0 && <div className="file-tree-item">No custom models yet.</div>}
                    {customModels.map((x) => (
                      <div className="file-tree-item" key={x.id}>
                        {x.label || x.id} ({x.auth_type || 'api_key'}) - {x.base_url}
                        <button className="clear-btn mini-btn" onClick={() => deleteCustomModel(x.id)}>Delete</button>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="admin-card">
                  <h3>Tool Integrations (N supported)</h3>
                  <div className="admin-keys-grid">
                    <div><label>id</label><input className="folder-input" value={integrationDraft.id} onChange={(e) => setIntegrationDraft((p) => ({ ...p, id: e.target.value }))} /></div>
                    <div><label>name</label><input className="folder-input" value={integrationDraft.name} onChange={(e) => setIntegrationDraft((p) => ({ ...p, name: e.target.value }))} /></div>
                    <div><label>endpoint</label><input className="folder-input" value={integrationDraft.endpoint} onChange={(e) => setIntegrationDraft((p) => ({ ...p, endpoint: e.target.value }))} /></div>
                    <div>
                      <label>auth_type</label>
                      <select className="folder-input" value={integrationDraft.auth_type} onChange={(e) => setIntegrationDraft((p) => ({ ...p, auth_type: e.target.value }))}>
                        <option value="none">none</option>
                        <option value="api_key">api_key</option>
                        <option value="bearer">bearer</option>
                        <option value="sso_profile">sso_profile</option>
                      </select>
                    </div>
                    <div><label>api_key</label><input className="folder-input" value={integrationDraft.api_key} onChange={(e) => setIntegrationDraft((p) => ({ ...p, api_key: e.target.value }))} /></div>
                    <div><label>secret / bearer token</label><input className="folder-input" value={integrationDraft.secret} onChange={(e) => setIntegrationDraft((p) => ({ ...p, secret: e.target.value }))} /></div>
                    <div><label>notes</label><input className="folder-input" value={integrationDraft.notes} onChange={(e) => setIntegrationDraft((p) => ({ ...p, notes: e.target.value }))} /></div>
                  </div>
                  <button className="load-btn" onClick={saveIntegration} disabled={adminLoading}>Save Integration</button>
                  <div className="file-tree">
                    {integrations.length === 0 && <div className="file-tree-item">No integrations yet.</div>}
                    {integrations.map((x) => (
                      <div className="file-tree-item" key={x.id}>
                        {x.name} ({x.auth_type}) — {x.endpoint}
                        <button className="clear-btn mini-btn" onClick={() => testIntegration(x.id)}>Test</button>
                        <button className="clear-btn mini-btn" onClick={() => deleteIntegration(x.id)}>Delete</button>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {adminError && (
              <div className="workspace-error">
                <AlertCircle size={14} /> {adminError}
              </div>
            )}
          </section>
        )}

        {/* Workspace Panel */}
        <section className="workspace-panel">
          <div className="workspace-header">
            <h2><FolderOpen className="icon" /> Workspace</h2>
            {workspace && (
              <span className="workspace-badge">
                <CheckCircle2 size={14} /> {workspace.file_count} files loaded
              </span>
            )}
          </div>

          <div className="workspace-input-row">
            <input
              type="text"
              className="folder-input"
              placeholder="Enter local folder path, e.g. C:\Projects\my-app or /home/user/my-app"
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleLoadFolder()}
              disabled={workspaceLoading}
            />
            <button
              className="load-btn"
              onClick={handleLoadFolder}
              disabled={workspaceLoading || !folderPath.trim()}
            >
              {workspaceLoading ? <Loader2 className="loader" size={16} /> : <FolderOpen size={16} />}
              {workspaceLoading ? 'Loading...' : 'Load'}
            </button>
            {workspace && (
              <button className="clear-btn" onClick={handleClearWorkspace}>
                Clear
              </button>
            )}
          </div>

          {workspaceError && (
            <div className="workspace-error">
              <AlertCircle size={14} /> {workspaceError}
            </div>
          )}

          {workspace && (
            <div className="file-tree-section">
              <button
                className="file-tree-toggle"
                onClick={() => setFileTreeOpen(!fileTreeOpen)}
              >
                {fileTreeOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <FileCode size={14} />
                {workspace.folder} &mdash; {workspace.file_count} files indexed
              </button>
              {fileTreeOpen && (
                <ul className="file-tree">
                  {workspace.file_tree.map((f) => (
                    <li key={f} className="file-tree-item">
                      <FileCode size={12} className="file-icon" /> {f}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>

        <main className="main-content">
          <section className="panel">
            <h2><Sparkles className="icon" /> Agent Prompt</h2>
            {workspace && (
              <div className="workspace-context-note">
                <CheckCircle2 size={13} /> Workspace active — agents will have full codebase context
              </div>
            )}
            <form onSubmit={handleSubmit} className="input-area">
              <textarea
                className="prompt-input"
                placeholder={workspace
                  ? `Describe what to build, fix, or review in ${workspace.folder}...`
                  : "Enter your instructions for the multi-agent system..."
                }
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={loading}
              />
              <label className="online-consent-row" title="Local-first: online providers only run if a local agent fails AND you tick this for the request.">
                <input
                  type="checkbox"
                  checked={onlineConsent}
                  onChange={(e) => setOnlineConsent(e.target.checked)}
                  disabled={loading}
                />
                <span>
                  Allow online assistance for this request only
                  <em>{onlineConsent ? ' (online fallback enabled)' : ' (off — strictly local)'}</em>
                </span>
              </label>
              <button
                type="submit"
                className="submit-btn"
                disabled={loading || !prompt.trim()}
              >
                {loading ? (
                  <><Loader2 className="loader" /> Processing...</>
                ) : (
                  <><Send size={20} /> Execute Task</>
                )}
              </button>
            </form>
            {error && <div className="error-message">{error}</div>}
            <div className="chat-history-panel">
              <div className="chat-history-header">
                <span className="chat-history-title"><History size={15} /> Chat History</span>
                <div className="chat-history-actions">
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => loadChatHistory()}
                    disabled={chatHistoryLoading}
                    title="Refresh chat history"
                  >
                    <RefreshCw size={14} />
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={clearChatHistory}
                    disabled={chatHistoryLoading || chatHistory.length === 0}
                    title="Clear chat history"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              {chatHistoryError && <div className="chat-history-error">{chatHistoryError}</div>}
              {chatHistoryLoading ? (
                <div className="chat-history-empty">Loading...</div>
              ) : recentChatTurns.length === 0 ? (
                <div className="chat-history-empty">No saved chats yet.</div>
              ) : (
                <div className="chat-history-list">
                  {recentChatTurns.map((turn) => (
                    <button
                      key={turn.user.id}
                      type="button"
                      className="chat-history-row"
                      onClick={() => restoreChatTurn(turn)}
                    >
                      <div className="chat-history-row-top">
                        <span>{previewText(turn.user.content)}</span>
                        <time>{formatChatTime(turn.user.created_at)}</time>
                      </div>
                      {turn.assistant?.content && (
                        <div className="chat-history-answer">{previewText(turn.assistant.content, 120)}</div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className="panel">
            <h2><Terminal className="icon" /> Output Console</h2>
            {setupRequired && (
              <div className="setup-required-panel">
                <div className="setup-required-title">
                  <AlertCircle size={15} /> Setup Required
                </div>
                <div className="setup-required-message">{setupRequired.message}</div>
                <ol className="setup-required-options">
                  {(setupRequired.detail?.options || []).map((opt) => (
                    <li key={opt.id} className={`setup-option setup-option-${opt.id}`}>
                      <div className="setup-option-title">
                        {opt.title}
                        {opt.ready && <span className="setup-option-ready">Ready</span>}
                      </div>
                      <ul className="setup-option-steps">
                        {(opt.steps || []).map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                      {opt.id === 'consent_online' && opt.ready && (
                        <button
                          type="button"
                          className="setup-option-action"
                          onClick={() => { setOnlineConsent(true); setSetupRequired(null); }}
                        >
                          Tick consent + retry
                        </button>
                      )}
                      {opt.id === 'ollama' && (
                        <button
                          type="button"
                          className="setup-option-action"
                          onClick={openSetupLocalAi}
                        >
                          Open Setup Local AI
                        </button>
                      )}
                      {opt.id === 'custom_local' && (
                        <button
                          type="button"
                          className="setup-option-action"
                          onClick={openSetupLocalAi}
                        >
                          Open Setup Local AI
                        </button>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {onlineModeBanner && (
              <div className="online-mode-banner">
                <Activity size={15} className="online-mode-icon" />
                <span>{onlineModeBanner}</span>
              </div>
            )}
            {showPipelinePanel && (
              <div className="pipeline-panel">
                <div className="pipeline-header">
                  <span className="pipeline-title">Pipeline Progress</span>
                  {requestIntents.length > 0 && (
                    <span className="pipeline-intents">
                      Intents: {requestIntents.join(', ')}
                    </span>
                  )}
                </div>
                <div className="pipeline-stages">
                  {Object.keys(pipelineStages).length === 0 && pipelineStarted && (
                    <div className="pipeline-stage pipeline-stage-queued">
                      <span className="pipeline-stage-icon">⋯</span>
                      <span className="pipeline-stage-name">stage details pending</span>
                    </div>
                  )}
                  {Object.entries(pipelineStages).map(([name, info]) => {
                    const status = info.status || 'queued';
                    const icon = (
                      status === 'done' ? '✓' :
                      status === 'running' ? '●' :
                      status === 'error' ? '!' :
                      status === 'skipped' ? '–' : '⋯'
                    );
                    return (
                      <div key={name} className={`pipeline-stage pipeline-stage-${status}`}>
                        <span className="pipeline-stage-icon">{icon}</span>
                        <div className="pipeline-stage-body">
                          <div className="pipeline-stage-row">
                            <span className="pipeline-stage-name">{name}</span>
                            {info.agent && <span className="pipeline-stage-agent">{info.agent}</span>}
                            {typeof info.duration_ms === 'number' && (
                              <span className="pipeline-stage-duration">{info.duration_ms} ms</span>
                            )}
                          </div>
                          {info.output_preview && (
                            <div className="pipeline-stage-preview">{info.output_preview}</div>
                          )}
                          {info.note && (
                            <div className="pipeline-stage-note">note: {info.note}</div>
                          )}
                          {info.error && (
                            <div className="pipeline-stage-error-msg">error: {info.error}</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
                {pipelineWarnings.length > 0 && (
                  <div className="pipeline-warnings">
                    {pipelineWarnings.map((w, i) => (
                      <div key={i} className="pipeline-warning"><AlertCircle size={13} /> {w}</div>
                    ))}
                  </div>
                )}
                {consentUsages.length > 0 && (
                  <div className="pipeline-consent-used">
                    {consentUsages.map((c, i) => (
                      <div key={i} className="pipeline-consent-row">
                        <Activity size={13} /> Online used for <strong>{c.intent}</strong> via {c.agent}
                        {typeof c.duration_ms === 'number' ? ` (${c.duration_ms} ms)` : ''}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {(loading || activeAgent || streamEvents.length > 0) && (
              <div className="stream-panel">
                <div className="stream-header">
                  <span className="stream-title">Live Execution</span>
                  {activeAgent && <span className="stream-agent">Active: {activeAgent}</span>}
                </div>
                <div className="stream-events">
                  {streamEvents.length === 0 && loading && <div>Starting stream...</div>}
                  {streamEvents.map((evt, idx) => <div key={`${evt}-${idx}`}>• {evt}</div>)}
                </div>
              </div>
            )}
            {finalAnswer && (
              <div className="final-answer-panel">
                <div className="final-answer-title">Final Response</div>
                <div className="final-answer-body">{finalAnswer}</div>
              </div>
            )}
            {runMetrics && (
              <div className="metrics-panel">
                <div className="metrics-title">Execution Metrics</div>
                <div className="metrics-grid">
                  <div className="metric-item"><span>Request</span><strong>{formatMs(runMetrics.request_ms)}</strong></div>
                  <div className="metric-item"><span>Process</span><strong>{formatMs(runMetrics.process_ms)}</strong></div>
                  <div className="metric-item"><span>Render</span><strong>{formatMs(runMetrics.render_ms)}</strong></div>
                  <div className="metric-item"><span>Total</span><strong>{formatMs(runMetrics.total_ms)}</strong></div>
                  <div className="metric-item"><span>Parallelism</span><strong>{formatParallelism(runMetrics.parallelism)}</strong></div>
                  <div className="metric-item metric-wide"><span>Models Used</span><strong>{(runMetrics.models || []).join(', ') || '-'}</strong></div>
                </div>
              </div>
            )}
            <div className="raw-output-section">
              <button
                className="raw-output-toggle"
                type="button"
                onClick={() => setRawOutputOpen((v) => !v)}
              >
                {rawOutputOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                {rawOutputOpen ? 'Hide Raw Output Console' : 'Show Raw Output Console'}
              </button>
              {rawOutputOpen && (
                result ? (
                  <div className="result-area">
                    {result}
                  </div>
                ) : (
                  <div className="result-placeholder">
                    <Terminal size={48} opacity={0.3} />
                    <p>Execution results will appear here</p>
                  </div>
                )
              )}
            </div>
          </section>
        </main>
      </div>

      {diagOpen && (
        <div className="diagnostics-overlay" role="dialog" aria-modal="true" aria-labelledby="diagnostics-title">
          <div className="diagnostics-modal">
            <div className="diagnostics-header">
              <h3 id="diagnostics-title"><Activity size={18} /> Diagnostics &amp; Traces</h3>
              <div className="diagnostics-header-actions">
                <button type="button" className="clear-btn mini-btn" onClick={fetchTraces} title="Refresh">
                  <RefreshCw size={14} /> Refresh
                </button>
                <button type="button" className="clear-btn mini-btn" onClick={() => setDiagOpen(false)} title="Close">
                  <XIcon size={14} />
                </button>
              </div>
            </div>

            {systemMetrics && (
              <div className="diagnostics-metrics">
                {systemMetrics.ram && (
                  <div className="diag-metric">
                    <span>RAM</span>
                    <strong>{systemMetrics.ram.free_gb} / {systemMetrics.ram.total_gb} GB free</strong>
                    <em>({systemMetrics.ram.used_pct}% used)</em>
                  </div>
                )}
                {systemMetrics.ollama && (
                  <div className="diag-metric">
                    <span>Ollama</span>
                    <strong>{systemMetrics.ollama.running ? 'up' : 'down'} ({systemMetrics.ollama.model_count} models)</strong>
                    {(systemMetrics.ollama.loaded || []).length > 0 && (
                      <em>loaded: {systemMetrics.ollama.loaded.map((m) => m.name).join(', ')}</em>
                    )}
                  </div>
                )}
                {systemMetrics.disks && Object.entries(systemMetrics.disks).map(([k, v]) => (
                  <div key={k} className="diag-metric">
                    <span>{k}</span>
                    <strong>{v.free_gb} / {v.total_gb} GB free</strong>
                    <em>{v.path}</em>
                  </div>
                ))}
              </div>
            )}

            <div className="diagnostics-body">
              <div className="diagnostics-list">
                <div className="diagnostics-list-header">Recent runs ({traces.length})</div>
                {tracesLoading && <div className="file-tree-item">Loading…</div>}
                {!tracesLoading && traces.length === 0 && <div className="file-tree-item">No traces yet — run a prompt and it will appear here.</div>}
                {traces.map((t) => (
                  <button
                    key={t.request_id}
                    type="button"
                    className={`diagnostics-list-row ${selectedTraceId === t.request_id ? 'selected' : ''} ${t.status === 'error' ? 'is-error' : ''}`}
                    onClick={() => fetchTraceDetail(t.request_id)}
                  >
                    <div className="diag-row-line1">
                      <span className={`diag-row-status diag-status-${t.status}`}>{t.status}</span>
                      <span className="diag-row-duration">{t.duration_ms} ms</span>
                      <span className="diag-row-id">{t.request_id}</span>
                    </div>
                    <div className="diag-row-line2">{t.prompt_preview || '(no prompt)'}</div>
                    <div className="diag-row-line3">
                      {(t.intents || []).join(', ') || '—'}
                      {' • '}
                      {(t.models_used || []).join(', ') || 'no models'}
                    </div>
                  </button>
                ))}
              </div>

              <div className="diagnostics-detail">
                {!selectedTraceId && (
                  <div className="diagnostics-detail-empty">Select a run on the left to inspect.</div>
                )}
                {selectedTraceId && !traceDetail && (
                  <div className="file-tree-item">Loading trace…</div>
                )}
                {traceDetail && traceDetail.error && (
                  <div className="error-message">{traceDetail.error}</div>
                )}
                {traceDetail && !traceDetail.error && (
                  <>
                    <div className="diagnostics-detail-actions">
                      <button type="button" className="load-btn mini-btn" onClick={copyTraceSummary} title="Copy a redacted human-readable summary"><CopyIcon size={14} /> Copy summary</button>
                      <button type="button" className="clear-btn mini-btn" onClick={copyTraceJson} title="Copy the full trace JSON"><CopyIcon size={14} /> Copy JSON</button>
                      <button type="button" className="clear-btn mini-btn" onClick={downloadTraceJson} title="Download the trace as a .json file"><Download size={14} /> Download</button>
                      {copyToast && <span className="diagnostics-toast">{copyToast}</span>}
                    </div>

                    <div className="diagnostics-detail-meta">
                      <div><strong>ID:</strong> {traceDetail.request_id}</div>
                      <div><strong>Status:</strong> {traceDetail.status}</div>
                      <div><strong>Duration:</strong> {traceDetail.duration_ms} ms</div>
                      <div><strong>Started:</strong> {traceDetail.started_at}</div>
                      <div><strong>Intents:</strong> {(traceDetail.intents || []).join(', ') || '—'}</div>
                      <div><strong>Models:</strong> {(traceDetail.models_used || []).join(', ') || '—'}</div>
                      <div><strong>Online mode:</strong> {traceDetail.online_pipeline_mode ? 'YES' : 'no'}</div>
                      <div><strong>Disk used:</strong> {traceDetail.disk_bytes_used} bytes</div>
                    </div>

                    <div className="diagnostics-detail-prompt">
                      <strong>Prompt:</strong>
                      <div>{traceDetail.prompt}</div>
                    </div>

                    <div className="diagnostics-detail-stages">
                      <strong>Stages</strong>
                      {(traceDetail.stages || []).map((s, i) => {
                        const attempts = (s.attempts || []).filter((a) => ['running', 'retry', 'done', 'error'].includes(a.status));
                        return (
                          <div key={i} className={`diag-stage diag-stage-${s.status}`}>
                            <div className="diag-stage-head">
                              <span className="diag-stage-name">{s.name}</span>
                              <span className="diag-stage-status">{s.status}</span>
                              {s.agent && <span className="diag-stage-agent">{s.agent}</span>}
                              {typeof s.duration_ms === 'number' && <span className="diag-stage-duration">{s.duration_ms} ms</span>}
                            </div>
                            {s.output_preview && <div className="diag-stage-preview">{s.output_preview}</div>}
                            {s.note && <div className="diag-stage-note">{s.note}</div>}
                            {s.error && <div className="diag-stage-error">error: {s.error}</div>}
                            {attempts.length > 1 && (
                              <div className="diag-stage-attempts">
                                <em>attempts:</em>
                                {attempts.map((a, j) => (
                                  <div key={j} className={`diag-attempt diag-attempt-${a.status}`}>
                                    [{a.status}] {a.agent || '—'} {typeof a.duration_ms === 'number' ? `(${a.duration_ms} ms)` : ''} {a.error ? `— ${a.error}` : ''}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    {traceDetail.error && (
                      <div className="diagnostics-detail-fatal">
                        <strong>Fatal error:</strong> {traceDetail.error}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {showUsageAgreement && (
        <div className="agreement-overlay" role="dialog" aria-modal="true" aria-labelledby="agreement-title">
          <div className="agreement-modal">
            <h3 id="agreement-title">AI Usage Agreement</h3>
            <p>
              You are about to share local workspace files for AI-assisted analysis. By continuing, you confirm you have
              permission to process this data and accept responsibility for reviewing AI-generated output before use.
              You agree to use this application lawfully and ethically. You are solely responsible for any misuse,
              non-compliant behavior, or illegal activity. Prismara AI and its operators are not liable for wrongful or
              unlawful practices performed by users.
            </p>
            <div className="agreement-actions">
              <button className="clear-btn" type="button" onClick={declineUsageAgreement}>Cancel</button>
              <button className="load-btn" type="button" onClick={acceptUsageAgreementAndContinue}>I Agree, Continue</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default App;
