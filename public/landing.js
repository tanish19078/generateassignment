const menu = document.getElementById('landing-menu');
const navigation = document.getElementById('landing-nav');
function closeLandingMenu() {
  navigation.classList.remove('open');
  menu.setAttribute('aria-expanded', 'false');
  menu.setAttribute('aria-label', 'Open navigation');
}
menu.addEventListener('click', () => {
  const open = navigation.classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
});
navigation.addEventListener('click', event => { if (event.target.closest('a')) closeLandingMenu(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeLandingMenu(); });

const previews = {
  os: {
    file: 'file_permissions.sh', document: 'File_Permissions.docx', mode: 'custom', action: 'Make an OS/CN/custom file',
    code: '# Explore file permissions in Linux\n\nstudent@ubuntu:~$ touch practical.txt\nstudent@ubuntu:~$ chmod 644 practical.txt\nstudent@ubuntu:~$ ls -l practical.txt\n\n-rw-r--r-- 1 student student 0 practical.txt\n\n# Read. Write. Understand.\nOwner    → read + write\nGroup    → read\nOthers   → read'
  },
  lab: {
    file: 'bubble_sort.py', document: 'Sorting_Lab.docx', mode: 'lab', action: 'Make a programming lab file',
    code: '# Aim: sort a list using bubble sort\n\ndef bubble_sort(values):\n    for end in range(len(values) - 1, 0, -1):\n        for i in range(end):\n            if values[i] > values[i + 1]:\n                values[i], values[i + 1] = values[i + 1], values[i]\n    return values\n\nprint(bubble_sort([8, 3, 5, 1]))\n\n# Sample output\n[1, 3, 5, 8]'
  },
  project: {
    file: 'project_report.outline', document: 'Project_Report.docx', mode: 'project', action: 'Make a project report',
    code: 'FRONTEND PROJECT REPORT\n\n01  Abstract\n02  Introduction\n03  Problem definition & requirements\n04  Proposed design / methodology\n05  Results with your screenshots\n06  References, when supplied\n\nYour repository facts → structured sections\nYour supplied prose  → preserved\nYour progress        → saved section by section'
  }
};
document.querySelectorAll('[data-demo]').forEach(button => {
  button.addEventListener('click', () => {
    const preview = previews[button.dataset.demo];
    if (!preview) return;
    document.querySelectorAll('[data-demo]').forEach(tab => {
      const active = tab === button;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', String(active));
    });
    document.getElementById('demo-file').textContent = preview.file;
    document.getElementById('demo-code').textContent = preview.code;
    document.getElementById('demo-document-name').textContent = preview.document;
    const link = document.getElementById('demo-start');
    link.href = `workspace.html?mode=${preview.mode}`;
    link.textContent = `${preview.action} →`;
  });
});
