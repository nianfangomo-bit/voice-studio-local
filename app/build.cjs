const path = require('path');
process.chdir(__dirname);
const dependencies = path.resolve(process.argv[2] || './node_modules');
const esbuild = require(path.join(dependencies, 'esbuild'));
esbuild.buildSync({entryPoints: ['ui/app.jsx'], bundle: true, outfile: 'static/app.js', minify: true,
  nodePaths: [dependencies], define: {'process.env.NODE_ENV': '"production"'}});
