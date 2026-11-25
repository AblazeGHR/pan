"""Explicit fixture provisioning; never monkeypatch production authentication."""
import secrets
import os
PASSWORD = os.environ.get('PAN_TEST_ADMIN_PASSWORD') or 'Aa1!' + secrets.token_urlsafe(24)


def provision(auth):
    if not auth.path.exists():
        auth.initialize(PASSWORD)
    return PASSWORD


def login_client(client, auth):
    """Provision an owned fixture authority and use its real HTTP login."""
    response = client.post('/api/automation/login', json={'key': provision(auth)},
                           headers={'Origin': str(client.base_url).rstrip('/')})
    assert response.status_code == 200, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']


def login_page(page, password):
    """Sign an owned browser into the production global login boundary."""
    page.get_by_label('管理员密码', exact=True).fill(password)
    page.get_by_role('button', name='登录', exact=True).click()
    page.get_by_role('heading', name='登录 Pan', exact=True).wait_for(state='hidden')
