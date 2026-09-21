#!/usr/bin/env python3
"""
Wiki页面发布器
调用studio-space-manager技能将报告发布到iCenter/ispace
"""

import subprocess
import re
import json
import os

from skill_paths import SKILL_STUDIO_SPACE, resolve_skill_dir

class WikiPublisher:
    def __init__(self):
        self.skill_dir = resolve_skill_dir(SKILL_STUDIO_SPACE)

    def parse_wiki_url(self, url: str) -> dict:
        """从Wiki URL解析space-id和parent-page-id"""
        # URL格式: https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view
        pattern = r'/space/([a-f0-9]+)/wiki/page/([a-f0-9]+)/view'
        match = re.search(pattern, url)
        if match:
            return {
                'space_id': match.group(1),
                'parent_page_id': match.group(2)
            }
        raise ValueError("无效的Wiki URL格式，应为: https://i.zte.com.cn/index/ispace/#/space/{spaceId}/wiki/page/{pageId}/view")

    def publish(self, content: str, title: str, parent_url: str, project_name: str) -> dict:
        """发布报告到Wiki"""
        try:
            # 解析URL
            parsed = self.parse_wiki_url(parent_url)
            space_id = parsed['space_id']
            parent_id = parsed['parent_page_id']

            # 构建完整标题
            full_title = f"{project_name} 5月工作量填报报告"

            # 保存内容到临时文件
            temp_file = f"/tmp/workload_report_{space_id}_{int(__import__('time').time())}.md"
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(content)

            # 调用studio-space-manager创建页面
            script_path = os.path.join(self.skill_dir, "scripts", "studio_space_page_save.py")
            if not os.path.exists(script_path):
                raise FileNotFoundError(f"未找到studio-space-manager脚本: {script_path}")

            cmd = f'python3 {script_path} --action create --space-id "{space_id}" --parent-id "{parent_id}" --title "{full_title}" --content-file "{temp_file}"'

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            # 清理临时文件
            try:
                os.remove(temp_file)
            except:
                pass

            if result.returncode != 0:
                raise Exception(f"Wiki发布失败: {result.stderr}")

            # 解析输出
            output = result.stdout.strip()
            try:
                data = json.loads(output)
                if data.get('success') or data.get('step') == 'created':
                    return {
                        'success': True,
                        'page_link': data.get('page_link'),
                        'page_id': data.get('contentId'),
                        'title': full_title
                    }
                else:
                    raise Exception(data.get('message', '未知错误'))
            except json.JSONDecodeError:
                # 非JSON输出，尝试提取链接
                if 'https://' in output:
                    link_match = re.search(r'(https://[^\s]+)', output)
                    if link_match:
                        return {
                            'success': True,
                            'page_link': link_match.group(1),
                            'title': full_title
                        }
                raise Exception(f"脚本返回格式异常: {output}")

        except Exception as e:
            return {'success': False, 'error': str(e)}

if __name__ == '__main__':
    # 测试
    publisher = WikiPublisher()
    test_content = "# 测试报告\n\n这是测试内容"
    result = publisher.publish(
        content=test_content,
        title="测试",
        parent_url="https://i.zte.com.cn/index/ispace/#/space/09726afd250f479a915ebb26c17cded5/wiki/page/5dc78949565611f185572b17345bf18e/view",
        project_name="ZENIC ONE-UME R22 V16.26.20"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
