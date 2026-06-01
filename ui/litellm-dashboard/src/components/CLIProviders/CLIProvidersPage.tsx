"use client";

/**
 * AIHub — CLI Providers tab
 *
 * Renders the CLI-based coding-agent providers detected by the AIHub
 * backend (claude, gemini, ...). Each card shows install + auth status,
 * with a Login button that drives the paste-code OAuth flow exposed by
 * `claude setup-token` and similar CLIs.
 *
 * Backend endpoints (registered by litellm/llms/cli_providers/web_ui.py
 * via the LITELLM_WORKER_STARTUP_HOOKS env var):
 *
 *   GET  /aihub/api/status                     all providers
 *   POST /aihub/api/providers/{id}/login       start OAuth, return URL
 *   POST /aihub/api/providers/{id}/submit      paste code, finish login
 *   POST /aihub/api/providers/{id}/logout      clear local session
 */

import React, { useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  LoginOutlined,
  LogoutOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { getProxyBaseUrl } from "@/components/networking";

const { Title, Text, Paragraph } = Typography;

interface Provider {
  id: string;
  name: string;
  icon: string;
  tagline: string;
  installed: boolean;
  auth: "ok" | "missing" | "unknown" | null;
  email: string | null;
  org: string | null;
  version: string | null;
  path: string | null;
}

interface LoginState {
  open: boolean;
  provider?: Provider;
  url?: string;
  sessionId?: string;
  code: string;
  submitting: boolean;
  error?: string;
}

const initialLoginState: LoginState = {
  open: false,
  code: "",
  submitting: false,
};

export default function CLIProvidersPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [login, setLogin] = useState<LoginState>(initialLoginState);

  const base = getProxyBaseUrl();

  // ── fetch all provider statuses
  const loadStatus = async () => {
    try {
      const res = await fetch(`${base}/aihub/api/status`);
      const data = await res.json();
      setProviders(data.providers || []);
    } catch (e) {
      message.error("Failed to load provider status");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  // ── start the login flow: subprocess spawned, OAuth URL returned
  const startLogin = async (provider: Provider) => {
    setLogin({
      open: true,
      provider,
      code: "",
      submitting: false,
    });
    try {
      const res = await fetch(
        `${base}/aihub/api/providers/${provider.id}/login`,
        { method: "POST" },
      );
      const data = await res.json();
      if (data.status === "waiting_code" && data.url && data.session_id) {
        setLogin((s) => ({ ...s, url: data.url, sessionId: data.session_id }));
      } else if (data.status === "not_installed") {
        setLogin((s) => ({ ...s, error: data.message }));
      } else {
        setLogin((s) => ({
          ...s,
          error: data.message || "Could not start the login flow.",
        }));
      }
    } catch (e: any) {
      setLogin((s) => ({ ...s, error: e?.message ?? String(e) }));
    }
  };

  // ── submit the pasted code into the running subprocess
  const submitCode = async () => {
    if (!login.sessionId || !login.code.trim() || !login.provider) return;
    setLogin((s) => ({ ...s, submitting: true, error: undefined }));
    try {
      const res = await fetch(
        `${base}/aihub/api/providers/${login.provider.id}/submit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: login.sessionId,
            code: login.code.trim(),
          }),
        },
      );
      const data = await res.json();
      if (data.status === "ok") {
        message.success(`Logged in as ${data.email || "user"}`);
        setLogin(initialLoginState);
        await loadStatus();
      } else {
        setLogin((s) => ({
          ...s,
          submitting: false,
          error: data.message || "Login did not complete.",
        }));
      }
    } catch (e: any) {
      setLogin((s) => ({
        ...s,
        submitting: false,
        error: e?.message ?? String(e),
      }));
    }
  };

  // ── confirm + logout
  const logout = (provider: Provider) => {
    Modal.confirm({
      title: `Logout ${provider.name}?`,
      content:
        "Anyone using AIHub will lose access to this provider until you log in again.",
      okText: "Logout",
      okType: "danger",
      cancelText: "Cancel",
      onOk: async () => {
        await fetch(
          `${base}/aihub/api/providers/${provider.id}/logout`,
          { method: "POST" },
        );
        await loadStatus();
        message.success(`${provider.name} logged out`);
      },
    });
  };

  const renderBadge = (p: Provider) => {
    if (!p.installed) {
      return (
        <Tag icon={<CloseCircleOutlined />} color="default">
          Not installed
        </Tag>
      );
    }
    if (p.auth === "ok") {
      return (
        <Tag icon={<CheckCircleOutlined />} color="success">
          Authenticated
        </Tag>
      );
    }
    if (p.auth === "missing") {
      return (
        <Tag icon={<ExclamationCircleOutlined />} color="gold">
          Login required
        </Tag>
      );
    }
    return <Tag color="default">Unknown</Tag>;
  };

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto" }}>
      <Title level={3} style={{ marginBottom: 4 }}>
        CLI Providers
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        CLI coding-agent providers detected on this AIHub node. Each card shows
        install + authentication status. Click <Text strong>Login</Text> to
        authenticate via the paste-code OAuth flow — works on remote servers
        without a local browser callback.
      </Paragraph>

      <Row gutter={[16, 16]}>
        {providers.map((p) => (
          <Col xs={24} sm={12} md={8} lg={8} xl={6} key={p.id}>
            <Card
              size="small"
              title={
                <Space>
                  <span style={{ fontSize: 18 }}>{p.icon}</span>
                  <Text strong>{p.name}</Text>
                </Space>
              }
              extra={renderBadge(p)}
              style={{ height: "100%" }}
            >
              <Paragraph
                type="secondary"
                style={{ marginBottom: 8, fontSize: 12 }}
              >
                {p.tagline}
              </Paragraph>

              {p.email && (
                <Text
                  type="secondary"
                  style={{ display: "block", fontSize: 12 }}
                >
                  {p.email}
                  {p.org && ` · ${p.org}`}
                </Text>
              )}
              {p.version && (
                <Text
                  type="secondary"
                  style={{
                    display: "block",
                    fontSize: 11,
                    marginTop: 4,
                  }}
                >
                  {p.version}
                </Text>
              )}

              <Space style={{ marginTop: 12 }}>
                {p.installed && p.auth !== "ok" && (
                  <Button
                    type="primary"
                    size="small"
                    icon={<LoginOutlined />}
                    onClick={() => startLogin(p)}
                  >
                    Login
                  </Button>
                )}
                {p.installed && p.auth === "ok" && (
                  <Button
                    danger
                    size="small"
                    icon={<LogoutOutlined />}
                    onClick={() => logout(p)}
                  >
                    Logout
                  </Button>
                )}
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={loadStatus}
                  loading={loading}
                >
                  Refresh
                </Button>
              </Space>
            </Card>
          </Col>
        ))}

        {!loading && providers.length === 0 && (
          <Col span={24}>
            <Card size="small">
              <Text type="secondary">
                No CLI providers configured. Add them in{" "}
                <Text code>litellm/llms/cli_providers/web_ui.py</Text>.
              </Text>
            </Card>
          </Col>
        )}
      </Row>

      {/* ── Login flow modal ── */}
      <Modal
        title={
          login.provider ? `Login — ${login.provider.name}` : "Login"
        }
        open={login.open}
        onCancel={() => setLogin(initialLoginState)}
        footer={null}
        width={620}
        destroyOnClose
      >
        {login.error ? (
          <Paragraph type="danger" style={{ marginBottom: 0 }}>
            {login.error}
          </Paragraph>
        ) : !login.url ? (
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Starting login flow…
          </Paragraph>
        ) : (
          <>
            <Paragraph>
              <Text strong>1.</Text> Open this URL in your browser:
            </Paragraph>
            <Paragraph
              copyable={{ text: login.url }}
              style={{ marginBottom: 16, wordBreak: "break-all" }}
            >
              <a
                href={login.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ fontSize: 12 }}
              >
                {login.url}
              </a>
            </Paragraph>

            <Paragraph>
              <Text strong>2.</Text> Sign in on the provider's site. After
              signing in you'll see a code on the result page.
            </Paragraph>

            <Paragraph>
              <Text strong>3.</Text> Copy the code, paste below, and click
              Submit:
            </Paragraph>

            <Space.Compact style={{ width: "100%" }}>
              <Input
                placeholder="paste code here"
                value={login.code}
                onChange={(e) =>
                  setLogin((s) => ({ ...s, code: e.target.value }))
                }
                onPressEnter={submitCode}
                autoFocus
                spellCheck={false}
              />
              <Button
                type="primary"
                loading={login.submitting}
                onClick={submitCode}
                disabled={!login.code.trim()}
              >
                Submit
              </Button>
            </Space.Compact>
          </>
        )}
      </Modal>
    </div>
  );
}
