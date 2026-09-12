from setuptools import setup
setup(name='ripple_edge',version='0.1.0',packages=['ripple_edge'],
      data_files=[('share/ament_index/resource_index/packages',['resource/ripple_edge']),('share/ripple_edge',['package.xml'])],
      install_requires=['setuptools','pydantic>=2','PyYAML','mcp==1.30.0'],entry_points={'console_scripts':['observe=ripple_edge.main:main','serve=ripple_edge.mcp_server:main']})
