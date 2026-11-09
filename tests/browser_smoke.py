"""Optional browser integration: run against a local app, never a live AI provider.

python tests/browser_smoke.py --url http://127.0.0.1:5055 --channel msedge --output .artifacts/browser
Requires Playwright, pytest (for existing fixtures), project dependencies and a browser.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright
from test_project_file import frontend_context, image_payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5055')
    parser.add_argument('--channel', default='msedge')
    parser.add_argument('--output', default='.artifacts/browser')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []

    def watch(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_default_timeout(15000)

    def shot(page, name):
        page.evaluate('document.fonts.ready')
        page.evaluate('window.scrollTo(0, 0)')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), name
        page.screenshot(path=str(output / f'{name}.png'), full_page=True)

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel=args.channel, headless=True)
        desktop = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page = desktop.new_page()
        watch(page)
        page.goto(args.url, wait_until='networkidle')
        assert 'pricing' not in page.locator('body').inner_text().lower()
        shot(page, 'landing-desktop')
        page.locator('[data-demo="lab"]').click()
        expect(page.locator('#demo-code')).to_contain_text('def bubble_sort')
        page.locator('[data-demo="project"]').click()
        expect(page.locator('#demo-start')).to_have_attribute('href', 'workspace.html?mode=project')
        page.locator('#demo-start').click()
        expect(page.locator('#view-setup')).to_be_visible()
        expect(page.locator('.mode-card')).to_have_count(3)
        expect(page.locator('[data-mode="project"]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-view="dashboard"]').click()
        shot(page, 'dashboard-desktop')
        page.locator('.quickstart-card').filter(has_text='Lab files').click()
        page.locator('#apiEndpoint').fill('https://example.invalid/v1')
        page.locator('#customModel').fill('test-model')
        page.locator('#apiKey').fill('test-key')
        page.locator('#codeLang').select_option('python')
        shot(page, 'setup-desktop')
        page.locator('#setupProceedBtn').click()
        page.locator('#aimsEditor').fill('Write a Python program to sort a list.\n---\nWrite a Python program to reverse a list.')
        expect(page.locator('#aimCountBadge')).to_contain_text('2 experiments')
        shot(page, 'aims-desktop')

        def lab_result(route):
            payload = route.request.post_data_json
            assert payload['base_url'] == 'https://example.invalid/v1'
            assert payload['model'] == 'test-model'
            route.fulfill(json={
                'aim': payload['aim'], 'concept': 'Python lists support ordered collections and explicit transformations.',
                'code': 'values = [8, 3, 5, 1]\nprint(sorted(values))', 'output': '[1, 3, 5, 8]',
                'caption': 'Sorted values', 'mode': 'general', 'code_language': 'python',
            })

        page.route('**/api/generate', lab_result)
        page.route('**/api/refine', lab_result)
        page.locator('#startGenBtn').click()
        expect(page.locator('#reviewBtn')).to_be_visible()
        shot(page, 'generation-desktop')
        page.locator('#reviewBtn').click()
        page.locator('.accordion-toggle').first.click()
        expect(page.locator('.accordion-toggle').first).to_have_attribute('aria-expanded', 'true')
        shot(page, 'review-desktop')
        page.get_by_role('button', name='REFINE', exact=True).first.click()
        expect(page.locator('#refineInput')).to_be_focused()
        shot(page, 'refine-desktop')
        page.locator('#refineInput').fill('Add comments explaining the sort.')
        page.get_by_role('button', name='Apply changes').click()
        expect(page.locator('#refine-modal')).to_be_hidden()
        page.get_by_role('button', name='Prepare download').click()
        shot(page, 'export-desktop')
        with page.expect_download() as transfer:
            page.locator('#downloadBtn').click()
        transfer.value.save_as(output / 'lab.docx')
        assert (output / 'lab.docx').read_bytes().startswith(b'PK')
        page.locator('#classroomToggle').check()
        expect(page.locator('#studentCount')).to_have_attribute('max', '12')
        expect(page.locator('#classroomOptions')).to_be_visible()

        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True,
                                     has_touch=True, reduced_motion='reduce')
        phone = mobile.new_page()
        watch(phone)
        phone.goto(args.url, wait_until='networkidle')
        shot(phone, 'landing-mobile')
        phone.locator('#landing-menu').click()
        expect(phone.locator('#landing-nav')).to_be_visible()
        phone.locator('#landing-nav').get_by_role('link', name='Features', exact=True).click()
        expect(phone.locator('#landing-menu')).to_have_attribute('aria-expanded', 'false')
        phone.goto(args.url + '/workspace.html', wait_until='networkidle')
        shot(phone, 'dashboard-mobile')
        phone.locator('#workspace-menu').click()
        expect(phone.locator('#sidebar')).to_be_visible()
        phone.keyboard.press('Escape')
        expect(phone.locator('#workspace-menu')).to_have_attribute('aria-expanded', 'false')
        phone.locator('.quickstart-card').filter(has_text='Project files').click()
        phone.locator('[data-project-submode="frontend"]').click()
        shot(phone, 'setup-mobile')
        phone.locator('#setupProceedBtn').click()
        context = frontend_context(with_sections=False)
        context['report_sections']['abstract'] = 'A concise abstract supplied by the project author.'
        phone.locator('#projectContextInput').fill(json.dumps(context))
        phone.locator('#validateProjectBtn').click()
        expect(phone.locator('#projectGenerateBtn')).to_be_enabled()
        shot(phone, 'project-context-mobile')
        section_calls = []
        failed = False

        def project_section(route):
            nonlocal failed
            field = route.request.post_data_json['section']
            section_calls.append(field)
            if field == 'introduction_significance' and not failed:
                failed = True
                route.fulfill(status=502, json={'error': 'Temporary section failure. Retry to resume.'})
                return
            route.fulfill(json={'section': field, 'text': (
                'The supplied student portal uses a component-oriented browser client and a JSON API. '
                'Its documented pages provide student academic workflows. This section describes the '
                'implementation from the provided facts without claiming independent execution.'
            )})

        phone.route('**/api/project/section', project_section)
        phone.locator('#projectGenerateBtn').click()
        expect(phone.get_by_role('button', name='Retry remaining sections')).to_be_visible()
        shot(phone, 'project-retry-mobile')
        phone.get_by_role('button', name='Retry remaining sections').click()
        expect(phone.locator('#reviewBtn')).to_be_visible()
        assert section_calls.count('introduction_background') == 1
        assert section_calls.count('introduction_significance') == 2
        phone.locator('#reviewBtn').click()
        phone.locator('.project-accordion-button').first.click()
        expect(phone.locator('.project-review-body').first).to_contain_text('A concise abstract supplied')
        shot(phone, 'project-review-mobile')
        phone.get_by_role('button', name='Prepare download').click()
        expect(phone.locator('#downloadBtn')).to_be_disabled()
        paths = []
        for result in context['project']['results']:
            filename = result['screenshot_filename']
            path = output / filename
            path.write_bytes(base64.b64decode(image_payload(filename)['data_base64']))
            paths.append(str(path))
        phone.locator('#projectImageInput').set_input_files(paths)
        expect(phone.locator('#downloadBtn')).to_be_enabled()
        shot(phone, 'project-export-mobile')
        with phone.expect_download() as transfer:
            phone.locator('#downloadBtn').click()
        transfer.value.save_as(output / 'project.docx')
        assert (output / 'project.docx').read_bytes().startswith(b'PK')
        phone.reload(wait_until='networkidle')
        phone.locator('#restoreBanner').get_by_role('button', name='Restore', exact=True).click()
        expect(phone.locator('#view-review')).to_be_visible()
        phone.get_by_role('button', name='Prepare download').click()
        expect(phone.locator('#downloadBtn')).to_be_disabled()
        expect(phone.locator('#projectExportImageStatus')).to_contain_text('Missing')
        phone.set_viewport_size({'width': 320, 'height': 740})
        expect(phone.locator('.step-item.active')).to_be_in_viewport()
        shot(phone, 'project-export-small-mobile')

        # Real deterministic CN generation and Word export, without an AI key.
        page.unroute('**/api/generate')
        page.goto(args.url + '/workspace.html?mode=custom', wait_until='networkidle')
        page.locator('#fileStyle').select_option('cn')
        expect(page.locator('#connection-card')).to_be_hidden()
        page.locator('#cnName').fill('Example Student')
        page.locator('#cnRoll').fill('42')
        page.locator('#setupProceedBtn').click()
        catalog = page.request.get(args.url + '/api/cn/catalog').json()
        page.locator('#aimsEditor').fill(catalog['experiments'][0]['aim'])
        page.locator('#startGenBtn').click()
        expect(page.locator('#reviewBtn')).to_be_visible()
        page.locator('#reviewBtn').click()
        page.locator('.accordion-toggle').first.click()
        expect(page.locator('.cn-figure img').first).to_be_visible()
        shot(page, 'cn-review-desktop')
        page.get_by_role('button', name='Prepare download').click()
        expect(page.locator('#classroom-panel')).to_be_hidden()
        with page.expect_download() as transfer:
            page.locator('#downloadBtn').click()
        transfer.value.save_as(output / 'cn.docx')
        assert (output / 'cn.docx').read_bytes().startswith(b'PK')
        assert not errors, errors
        browser.close()
    print('Browser checks passed: landing, desktop/mobile, lab refine/export, Project failure/resume/images/restore/export, and live CN export. No uncaught browser errors.')


if __name__ == '__main__':
    main()
