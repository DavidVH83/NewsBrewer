# Security Policy

## Supported Versions

NewsBrewer is currently in active development. Security fixes are applied to the latest version only.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub Issues.**

If you discover a security vulnerability, please report it privately:

1. Go to the **Security** tab in this repository
2. Click **Report a vulnerability**
3. Provide a clear description of the vulnerability and steps to reproduce it

You can expect an acknowledgement within 48 hours and a resolution timeline within 7 days for critical issues.

## Security Best Practices for Users

- Never commit your `config.yml` or `.env` files — they are git-ignored by default
- Use Gmail/Outlook **app passwords**, never your main account password
- Store all credentials as **GitHub Secrets**, never in the repository
- Regularly rotate your GitHub Personal Access Token used for GitHub Models
- The `CONFIG_YML` secret contains your personal preferences — treat it as sensitive
