# Operations Guide

## Service Management Commands

- **Start**: `systemctl start multi-exchange-paper.service`
- **Stop**: `systemctl stop multi-exchange-paper.service`
- **Restart**: `systemctl restart multi-exchange-paper.service`
- **Inspect Status**: `systemctl status multi-exchange-paper.service --no-pager`
- **Stream Logs**: `journalctl -u multi-exchange-paper.service -f`
- **Engine JSON Log**: `tail -f /root/multi-exchange-paper/logs/engine.log`

## Verification & Testing

- **Run Automated Tests**:
  `cd /root/multi-exchange-paper && /root/multi-exchange-paper/.venv/bin/pytest -v`
- **Health Check**:
  `curl -sS http://127.0.0.1:8788/api/health`
- **Database Backup**:
  `/root/multi-exchange-paper/scripts/backup_db.sh`

## Dashboard Access

The dashboard listens on `127.0.0.1:8788` with HTTP Basic Authentication (credentials stored in `/root/trading-secrets/dashboard.env` with `0600` permissions). Access securely via SSH port forwarding:

```bash
ssh -L 8788:127.0.0.1:8788 root@srv2115613
```

Then open `http://127.0.0.1:8788` in your browser.
