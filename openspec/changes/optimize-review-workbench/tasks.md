## 1. Durable review status

- [x] 1.1 Persist the ticket after a review decision changes its status, and verify the adapter-backed entity reloads as `reviewed`
- [x] 1.2 Add an API integration regression test covering reviewer submission, admin list visibility, and removal from the pending review queue without an API restart
- [x] 1.3 Return the latest review labels and primary queue in the administrator list and verify they replace the initial prediction after review

## 2. Review workbench layout

- [x] 2.1 Add compact review-detail markup and scoped styles for bounded scrolling, smaller controls, and a responsive one-column fallback; verify the frontend type check and production build

## 3. Deployment verification

- [x] 3.1 Rebuild the VM Compose API, Worker, and Web services and verify health endpoints plus the reviewer-to-admin status flow against `192.168.88.100`
